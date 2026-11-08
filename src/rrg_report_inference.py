"""Unified inference on complete months in the fixed report-period experiment."""
import argparse
from itertools import combinations
import json
from pathlib import Path
import platform

import arch
import numpy as np
import pandas as pd
import statsmodels
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests

from rrg_mechanism_statistics import hac,bootstrap_pair

TARGET=Path(__file__).resolve().parents[1]/'results/rrg_report_settings_20261107/diffusion_etf/four_strategy_robustness'


def safe_hac(y,label,lag=3):
    values=np.asarray(y,dtype=float)
    if np.all(np.abs(values)<1e-14):
        return dict(comparison=label,n=len(values),HAC_lags=lag,mean_monthly_difference=0.,
            standard_error=0.,t_stat=0.,p_two_sided=1.,ci95_low=0.,ci95_high=0.)
    return hac(values,label,lag)


def main(target=TARGET):
    target=Path(target)
    run=json.loads((target/'run.json').read_text())
    r=pd.read_csv(target/'monthly_returns_complete.csv',index_col=0,parse_dates=True)
    assert len(r)==87 and r.notna().all().all()
    assert r.index[0]==pd.Timestamp('2019-01-31') and r.index[-1]==pd.Timestamp('2026-03-31')
    names=run['primary_strategies']
    all_tests,boots=[],[]
    for a,b in combinations(names,2):
        label=a+' minus '+b
        for lag in [1,3,6]:
            all_tests.append(safe_hac(r[a]-r[b],label,lag))
        for block in [3,6]:
            boots.append(bootstrap_pair(r[a],r[b],label,block,252/run['full_sample_trading_days']))
    tests=pd.DataFrame(all_tests)
    primary=tests.loc[tests.HAC_lags.eq(3)].copy()
    primary['p_Holm_6']=multipletests(primary.p_two_sided,method='holm')[1]
    assert np.isfinite(primary.p_two_sided).all()
    tests.to_csv(target/'all_HAC_tests.csv',index=False)
    primary.to_csv(target/'primary_pairwise_tests.csv',index=False)
    pd.DataFrame(boots).to_csv(target/'paired_block_bootstrap.csv',index=False)
    cost_tests=[]
    for bp in [3,5,10,20]:
        cost_returns={}
        for name in names:
            nav=pd.read_csv(target/f'{name}_{bp}bp_nav.csv',index_col=0,parse_dates=True).iloc[:,0]
            cost_returns[name]=nav.resample('ME').last().pct_change(fill_method=None).loc['2019-01-01':]
        for a,b in combinations(names,2):
            row=safe_hac(cost_returns[a]-cost_returns[b],a+' minus '+b,3)
            row['one_way_bp']=bp
            cost_tests.append(row)
    cost_tests=pd.DataFrame(cost_tests)
    assert len(cost_tests)==24 and np.isfinite(cost_tests.p_two_sided).all()
    cost_tests['p_Holm_24']=multipletests(cost_tests.p_two_sided,method='holm')[1]
    cost_tests.to_csv(target/'cost_pairwise_tests.csv',index=False)

    grid=pd.read_csv(target/'grid_monthly_returns.csv',index_col=0,parse_dates=True)
    assert grid.shape==(87,27) and grid.index.equals(r.index)
    active=names[:3]
    family=[]
    for lookback in [180,220,260]:
        for n in [4,6,8]:
            series={a:grid[f'{a}_{lookback}_{n}'] for a in active}
            for a,b in combinations(active,2):
                row=safe_hac(series[a]-series[b],f'{a} minus {b}')
                row.update(lookback=lookback,max_industries=n)
                family.append(row)
            for a in active:
                row=safe_hac(series[a]-r.ETF_equal_weight,f'{a} minus ETF_equal_weight')
                row.update(lookback=lookback,max_industries=n)
                family.append(row)
    family=pd.DataFrame(family)
    assert len(family)==54 and np.isfinite(family.p_two_sided).all()
    family['p_Holm_54']=multipletests(family.p_two_sided,method='holm')[1]
    family.to_csv(target/'grid_multiple_tests.csv',index=False)
    regressions=[]
    X=sm.add_constant(r[['Industry_momentum_220_6','ETF_equal_weight']])
    for name in ['Pure_RRG_Q1','Report_base']:
        fit=sm.OLS(r[name],X).fit(cov_type='HAC',cov_kwds={'maxlags':3,'use_correction':True},use_t=True)
        ci=fit.conf_int().loc['const']
        regressions.append(dict(strategy=name,monthly_intercept=float(fit.params['const']),
            t_intercept=float(fit.tvalues['const']),p_intercept=float(fit.pvalues['const']),
            ci95_low=float(ci.iloc[0]),ci95_high=float(ci.iloc[1]),
            beta_momentum=float(fit.params['Industry_momentum_220_6']),beta_equal_weight=float(fit.params['ETF_equal_weight']),
            r_squared=float(fit.rsquared),condition_number=float(fit.condition_number)))
    regressions=pd.DataFrame(regressions)
    regressions['p_Holm_2']=multipletests(regressions.p_intercept,method='holm')[1]
    regressions.to_csv(target/'benchmark_adjusted_regression.csv',index=False)
    (target/'inference_validation.json').write_text(json.dumps(dict(complete_months=87,
        primary_comparisons=6,HAC_sensitivity_rows=18,bootstrap_rows=12,bootstrap_reps=10000,
        parameter_family_tests=54,cost_sensitivity_tests=24,
        primary_Holm_rejections=int(primary.p_Holm_6.lt(.05).sum()),
        cost_Holm_rejections=int(cost_tests.p_Holm_24.lt(.05).sum()),
        grid_Holm_rejections=int(family.p_Holm_54.lt(.05).sum()),
        mean_gaps_reconcile=True,
        inference_environment={'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,
            'statsmodels':statsmodels.__version__,'arch':arch.__version__},
        statistical_scope='Mean non-overlapping monthly net-return differences. HAC(3), small-sample correction and t approximation. Bootstrap conditional on observed sample; not independent validation. Adjusted regressions are benchmark intercepts, not FF alpha.',
        multiplicity_scope='Families cover the current six primary pairs and 54 grid pairs separately, not all prior research choices.'),ensure_ascii=False,indent=2))
    print(primary.to_string(index=False),flush=True)
    print('REGRESSION\n'+regressions.to_string(index=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path,default=TARGET)
    main(parser.parse_args().target)
