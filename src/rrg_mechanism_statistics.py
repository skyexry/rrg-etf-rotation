"""Inference from exported CSVs; use installed statsmodels and arch libraries."""
import json
import platform
from pathlib import Path

import arch
from arch.bootstrap import CircularBlockBootstrap
import numpy as np
import pandas as pd
import statsmodels
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests

TARGET=Path(__file__).resolve().parents[1]/'results/rrg/author_baseline_comparison/ablation/q1_mechanism_significance'
SEED,REPS=20261107,10000


def hac(y,label,lag=3):
    y=np.asarray(y,dtype=float)
    y=y[np.isfinite(y)]
    fit=sm.OLS(y,np.ones((len(y),1))).fit(cov_type='HAC',
        cov_kwds={'maxlags':lag,'use_correction':True},use_t=True)
    np.testing.assert_allclose(fit.params[0],y.mean(),atol=1e-12)
    ci=fit.conf_int()[0]
    return dict(comparison=label,n=len(y),HAC_lags=lag,mean_monthly_difference=float(fit.params[0]),
        standard_error=float(fit.bse[0]),t_stat=float(fit.tvalues[0]),p_two_sided=float(fit.pvalues[0]),
        ci95_low=float(ci[0]),ci95_high=float(ci[1]))


def bootstrap_pair(a,b,label,block,annual_exponent):
    paired=np.column_stack([a,b])
    observed_mean=float(np.mean(paired[:,0]-paired[:,1]))
    cagr_gap=float(np.prod(1+paired[:,0])**annual_exponent-np.prod(1+paired[:,1])**annual_exponent)
    means,cagrs=np.empty(REPS),np.empty(REPS)
    for i,(pos,_) in enumerate(CircularBlockBootstrap(block,paired,seed=SEED).bootstrap(REPS)):
        draw=pos[0]
        means[i]=np.mean(draw[:,0]-draw[:,1])
        cagrs[i]=np.prod(1+draw[:,0])**annual_exponent-np.prod(1+draw[:,1])**annual_exponent
    centered=paired[:,0]-paired[:,1]-observed_mean
    null=np.array([np.mean(pos[0]) for pos,_ in CircularBlockBootstrap(block,centered,seed=SEED).bootstrap(REPS)])
    return dict(comparison=label,block_months=block,reps=REPS,observed_mean_monthly_gap=observed_mean,
        mean_gap_ci95_low=float(np.quantile(means,.025)),mean_gap_ci95_high=float(np.quantile(means,.975)),
        mean_gap_centered_bootstrap_p=float((1+np.sum(np.abs(null)>=abs(observed_mean)))/(REPS+1)),
        observed_cagr_gap=cagr_gap,cagr_gap_ci95_low=float(np.quantile(cagrs,.025)),
        cagr_gap_ci95_high=float(np.quantile(cagrs,.975)))


def main():
    target=TARGET
    config=json.loads((target/'mechanism_inputs.json').read_text())
    returns=pd.read_csv(target/'monthly_net_returns.csv',index_col=0,parse_dates=True)
    assert len(returns)==48 and returns.notna().all().all()
    pairs=[('Q1_180_6','Q1_260_6'),('Q1_220_6','Industry_momentum_220_6'),
        ('Q1_220_6','ETF_equal_weight'),('Q1_180_6','Industry_momentum_220_6'),('Q1_180_6','ETF_equal_weight')]
    tests,boots=[],[]
    for a,b in pairs:
        label=a+' minus '+b
        for lag in [1,3,6]:
            tests.append(hac(returns[a]-returns[b],label,lag))
        for block in [3,6]:
            boots.append(bootstrap_pair(returns[a],returns[b],label,block,252/968))
    pd.DataFrame(tests).to_csv(target/'paired_HAC_tests.csv',index=False)
    pd.DataFrame(boots).to_csv(target/'paired_block_bootstrap.csv',index=False)

    family=[]
    for q in [c for c in returns if c.startswith('Q1_')]:
        for baseline in ['Industry_momentum_220_6','ETF_equal_weight']:
            family.append(hac(returns[q]-returns[baseline],q+' minus '+baseline,3))
    family=pd.DataFrame(family)
    family['p_Holm_18']=multipletests(family.p_two_sided,method='holm')[1]
    family.to_csv(target/'grid_multiple_tests.csv',index=False)
    regression=[]
    X=sm.add_constant(returns[['Industry_momentum_220_6','ETF_equal_weight']])
    for q in ['Q1_180_6','Q1_220_6','Q1_260_6']:
        fit=sm.OLS(returns[q],X).fit(cov_type='HAC',cov_kwds={'maxlags':3,'use_correction':True},use_t=True)
        ci=fit.conf_int().loc['const']
        regression.append(dict(strategy=q,n=len(returns),monthly_intercept=float(fit.params['const']),
            t_intercept=float(fit.tvalues['const']),p_intercept=float(fit.pvalues['const']),
            ci95_low=float(ci.iloc[0]),ci95_high=float(ci.iloc[1]),
            beta_momentum=float(fit.params['Industry_momentum_220_6']),
            beta_equal_weight=float(fit.params['ETF_equal_weight']),r_squared=float(fit.rsquared),
            condition_number=float(fit.condition_number)))
    regression=pd.DataFrame(regression)
    regression['p_Holm_3']=multipletests(regression.p_intercept,method='holm')[1]
    regression.to_csv(target/'benchmark_adjusted_regression.csv',index=False)

    events=pd.read_csv(target/'turning_point_events.csv',parse_dates=['event_date'])
    dates=pd.to_datetime(pd.read_csv(target/'valid_formation_months.csv').formation_month)
    timing=[]
    for (reference,direction),e in events.groupby(['reference_days','direction']):
        d=e.assign(delay_difference=e.delay_180-e.delay_260,
            immediate_difference=e.immediate_180-e.immediate_260,
            within3_difference=e.within3_180-e.within3_260)
        bymonth=d.groupby('event_date').agg(diff_sum=('delay_difference','sum'),
            immediate_sum=('immediate_difference','sum'),within3_sum=('within3_difference','sum'),
            count=('delay_difference','size')).reindex(dates).fillna(0.)
        values=bymonth[['diff_sum','immediate_sum','within3_sum','count']].to_numpy()
        boot=[]
        for pos,_ in CircularBlockBootstrap(3,values,seed=SEED).bootstrap(REPS):
            totals=pos[0].sum(axis=0)
            if totals[3]>0:
                boot.append(totals[:3]/totals[3])
        boot=np.asarray(boot)
        timing.append(dict(reference_days=int(reference),direction=direction,events=len(e),
            event_months=e.event_date.nunique(),mean_capped_delay180=float(e.delay_180.mean()),
            mean_capped_delay260=float(e.delay_260.mean()),delay_difference180_minus260=float((e.delay_180-e.delay_260).mean()),
            cluster_block_ci95_low=float(np.quantile(boot[:,0],.025)),cluster_block_ci95_high=float(np.quantile(boot[:,0],.975)),
            immediate_gap_ci95_low=float(np.quantile(boot[:,1],.025)),immediate_gap_ci95_high=float(np.quantile(boot[:,1],.975)),
            within3_gap_ci95_low=float(np.quantile(boot[:,2],.025)),within3_gap_ci95_high=float(np.quantile(boot[:,2],.975)),
            immediate_rate180=float(e.immediate_180.mean()),immediate_rate260=float(e.immediate_260.mean()),
            within3_rate180=float(e.within3_180.mean()),within3_rate260=float(e.within3_260.mean())))
    pd.DataFrame(timing).to_csv(target/'turning_point_summary.csv',index=False)
    payoff=pd.read_csv(target/'industry_forward_payoffs.csv')
    forward=hac(payoff.industry_ew_difference,'180 minus 260, equal-weight selected industry forward returns',3)
    forward.update(mean_forward_return180=float(payoff.industry_ew180.mean()),
        mean_forward_return260=float(payoff.industry_ew260.mean()),
        mean_exclusive_return180=float(payoff.exclusive_return180.mean()),
        mean_exclusive_return260=float(payoff.exclusive_return260.mean()),
        exclusive_comparable_months=int(payoff[['exclusive_return180','exclusive_return260']].notna().all(axis=1).sum()))
    pd.DataFrame([forward]).to_csv(target/'industry_forward_payoff_test.csv',index=False)
    pd.DataFrame([hac(payoff.industry_ew_difference,'industry forward selection gap',lag)
        for lag in [1,3,6]]).to_csv(target/'industry_forward_HAC_sensitivity.csv',index=False)
    forward_boot=[]
    for block in [3,6]:
        result=bootstrap_pair(payoff.industry_ew180,payoff.industry_ew260,'industry forward selection gap',block,12/len(payoff))
        forward_boot.append({k:v for k,v in result.items() if 'cagr' not in k})
    pd.DataFrame(forward_boot).to_csv(target/'industry_forward_block_bootstrap.csv',index=False)

    validation=dict(all_HAC_means_reconcile=True,monthly_observations=len(returns),
        tests_in_current_grid_family=len(family),bootstrap_replicates=REPS,
        number_of_turn_event_groups=len(timing),forward_months=len(payoff),
        important_scope='HAC is an approximation with 48 months. Holm covers the current 18 tests, not the whole previous search. Block bootstrap is conditional on the observed strategies.',
        statistic='Mean monthly return gap (not CAGR alpha); regressions are benchmark-adjusted intercepts, not FF factor alphas.',
        inference_environment={'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__,
            'statsmodels':statsmodels.__version__,'arch':arch.__version__},
        source_config=config,
        method_sources=['https://www.statsmodels.org/stable/generated/statsmodels.regression.linear_model.RegressionResults.get_robustcov_results.html',
            'https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html',
            'https://bashtage.github.io/arch/bootstrap/timeseries-bootstraps.html'])
    (target/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2))
    print('PRIMARY HAC\n'+pd.DataFrame(tests).query('HAC_lags==3').to_string(index=False),flush=True)
    print('TIMING\n'+pd.DataFrame(timing).to_string(index=False),flush=True)
    print('FORWARD\n'+pd.DataFrame([forward]).to_string(index=False),flush=True)
    print('REGRESSION\n'+regression.to_string(index=False),flush=True)


if __name__=='__main__':
    main()
