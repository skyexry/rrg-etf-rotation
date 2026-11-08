"""Export fixed mechanism diagnostics in the original RRG environment."""
import hashlib
import json
import platform

import numpy as np
import pandas as pd

from rrg_pipeline import OUT,SIGNAL_START,BACKTEST_START,END,EXCLUDE,bind_upstream,verify_upstream,write_json


def main():
    source=OUT/'author_baseline_comparison'
    grid=source/'ablation/q1_parameter_checks'
    target=source/'ablation/q1_mechanism_significance'
    target.mkdir(parents=True,exist_ok=True)
    run=json.loads((grid/'run.json').read_text())
    with open(run['input_database'],'rb') as f:
        assert hashlib.file_digest(f,'sha256').hexdigest()==run['input_database_sha256']
    assert verify_upstream()==run['upstream_commit']
    bind_upstream(run['input_database'])
    from src.data_provider import fetch_rrg_dataset,fetch_zx_industry_data
    from src.generator import RRGSignalGenerator
    benchmark,close,names=fetch_rrg_dataset(start_date=SIGNAL_START,end_date=END,classification='zx')
    close=close.drop(columns=EXCLUDE)
    prices=fetch_zx_industry_data(SIGNAL_START,END,fields=('close','open'))
    opening=prices.pivot(index='trade_date',columns='code',values='open').reindex(columns=close.columns)
    relative=close.div(benchmark,axis=0)
    navs={}
    for lookback in [180,220,260]:
        for n in [4,6,8]:
            label=f'Q1_{lookback}_{n}'
            navs[label]=pd.read_csv(grid/f'{label}_nav.csv',index_col=0,parse_dates=True).iloc[:,0]
    for label in ['Industry_momentum_220_6','ETF_equal_weight']:
        navs[label]=pd.read_csv(source/f'{label}_sell_first_nav.csv',index_col=0,parse_dates=True).iloc[:,0]
    nav=pd.DataFrame(navs)
    assert nav.notna().all().all() and nav.index.is_unique
    month_end=nav.resample('ME').last()
    monthly=month_end.pct_change(fill_method=None)
    monthly.iloc[0]=month_end.iloc[0]/run['initial_cash']-1
    monthly.to_csv(target/'monthly_net_returns.csv')
    monthly.to_parquet(target/'monthly_net_returns.parquet')

    signals={}
    eligible={}
    for lookback in [180,260]:
        gen=RRGSignalGenerator(start_date=SIGNAL_START,end_date=END,classification='zx',
            lookback_ratio=lookback,lookback_mom=60,smooth_window=20)
        ratio,mom=gen.get_rrg()
        eligible[lookback]=(ratio.gt(100)&mom.gt(100)).reindex(columns=close.columns)
        signals[lookback]=pd.read_parquet(grid/f'Q1_{lookback}_6_signals.parquet').astype(bool)
        fresh=gen.generate('quadrant',quadrants=(1,),top_n=6,exclude=EXCLUDE).reindex(signals[lookback].index).fillna(False).astype(bool)
        assert np.array_equal(fresh.to_numpy(),signals[lookback].to_numpy())
    formation=signals[180].index
    # Last Aug31 signal is not executed inside the sample and has no index quote.
    valid=formation[(formation>=pd.Timestamp(BACKTEST_START))&(formation<nav.index[-1])]
    valid=valid[valid.isin(close.index)]
    timing=[]
    for reference_window in [63,126]:
        reference=relative.pct_change(reference_window,fill_method=None).reindex(formation)
        for i,d in enumerate(formation):
            if d not in valid or i==0 or i+3>=len(formation):
                continue
            window=formation[i:i+4]
            if not window.isin(valid).all():
                continue
            prev=formation[i-1]
            for code in close.columns:
                now,before=reference.at[d,code],reference.at[prev,code]
                if pd.isna(now) or pd.isna(before):
                    continue
                direction='up' if before<=0<now else 'down' if before>=0>now else None
                if direction is None:
                    continue
                # Compare entry only when both were previously out, and exit
                # only when both were previously in: a common starting state.
                wanted_previous=(direction=='down')
                if not all(bool(signals[l].at[prev,code])==wanted_previous for l in [180,260]):
                    continue
                row={'reference_days':reference_window,'direction':direction,'event_date':d,
                     'industry':code,'industry_name':names.get(code,code)}
                for l in [180,260]:
                    state=signals[l].loc[window,code]
                    response=state if direction=='up' else ~state
                    positions=np.flatnonzero(response.to_numpy())
                    # Four means no response by three subsequent formations,
                    # not a claim that the actual response occurs at month 4.
                    delay=int(positions[0]) if len(positions) else 4
                    row[f'delay_{l}']=delay
                    row[f'immediate_{l}']=int(delay==0)
                    row[f'within3_{l}']=int(delay<=3)
                timing.append(row)
    pd.DataFrame(timing).to_csv(target/'turning_point_events.csv',index=False)
    pd.Series(valid.astype(str),name='formation_month').to_csv(target/'valid_formation_months.csv',index=False)

    payoff=[]
    for d in valid:
        idx=formation.get_indexer([d])[0]
        if idx+1>=len(formation):
            continue
        end=formation[idx+1]
        after=nav.index[nav.index>d]
        if not len(after) or after[0] not in opening.index or end not in close.index:
            continue
        entry=after[0]
        future=close.loc[end]/opening.loc[entry]-1
        assert future.notna().all()
        a=signals[180].loc[d].reindex(close.columns)
        b=signals[260].loc[d].reindex(close.columns)
        row={'formation_date':d,'entry_date':entry,'exit_mark_date':end,
             'n180':int(a.sum()),'n260':int(b.sum()),'industry_benchmark_return':float(future.mean()),
             'industry_ew180':float(future[a].mean()),'industry_ew260':float(future[b].mean()),
             'exclusive_n180':int((a&~b).sum()),'exclusive_n260':int((b&~a).sum()),
             'exclusive_return180':float(future[a&~b].mean()),'exclusive_return260':float(future[b&~a].mean())}
        row['industry_ew_difference']=row['industry_ew180']-row['industry_ew260']
        payoff.append(row)
    pd.DataFrame(payoff).to_csv(target/'industry_forward_payoffs.csv',index=False)
    assert len(payoff)>=40
    # Quantify whether ETF implementation rather than industry selection alone
    # accounts for the performance gap; the index portfolios are nontradable proxies.
    descriptions={}
    for l in [180,260]:
        label=f'Q1_{l}_6'
        r=nav[label].pct_change().fillna(nav[label].iloc[0]/1e7-1)
        descriptions[label]={'cagr':float((nav[label].iloc[-1]/1e7)**(252/len(nav))-1),
            'annual_volatility':float(r.std(ddof=1)*np.sqrt(252))}
    write_json(target/'mechanism_inputs.json',dict(run,
        comparisons=descriptions,source_environment={'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__},
        timing_reference='Past 63-session relative industry return crosses zero; 126-session secondary check. Neither reference uses 180/260 lookback.',
        timing_start_state='Upturn: both unselected at previous formation. Downturn: both selected at previous formation.',
        timing_horizon='Current formation through 3 subsequent formations; no response coded 4, a capped score only.',
        outcome_scope='Industry next-session open to next-month formation close; equal-weight selected indices, gross of costs. Missing terminal industry quote excluded equally.',
        mechanism_boundary='Observational proxy tests, not independent ground-truth turning dates or causal mediation.',
        inferential_plan={'monthly_HAC_lags_primary':3,'HAC_lag_sensitivity':[1,3,6],
            'two_sided_alpha':.05,'circular_block_lengths':[3,6],'bootstrap_replicates':10000,
            'seed':20261107,'multiplicity':'Holm across current 9 Q1 settings x 2 fixed baselines; does not cover prior strategy search.'}))
    print(f'Exported {len(monthly)} non-overlapping months, {len(timing)} turn events, {len(payoff)} forward-payoff periods',flush=True)


if __name__=='__main__':
    main()
