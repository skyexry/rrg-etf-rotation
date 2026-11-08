"""Unified report-period time, cost and parameter checks for four portfolios."""
import argparse
import gc
import json
import platform

import duckdb
import numpy as np
import pandas as pd

from rrg_pipeline import EXCLUDE,bind_upstream,verify_upstream,write_json
from rrg_report_tushare import OUT as SOURCE,DB,PARENT,START,END,WARMUP,sha,validate


def main(repaired=False):
    source,database=SOURCE,DB
    if repaired:
        from rrg_repair_inputs import OUT as source, DB as database
    target=source/'four_strategy_robustness'
    target.mkdir(parents=True,exist_ok=True)
    run=json.loads((source/'run.json').read_text())
    assert sha(database)==run['input_sha256'] and verify_upstream()==run['upstream_commit']
    bind_upstream(database)
    from src.data_provider import fetch_etf_meta,fetch_etf_daily,fetch_rrg_dataset
    from src.backtest import build_exposure_panel,mask_listed,run_vbt_backtest,portfolio_stats
    from src.strategy import select_by_quadrant,select_diffusion_with_rrg,select_by_diffusion
    from src.optimizer import batch_optimize
    from src.factor_algo import compute_rrg,diffusion_value_weighted,_up_flags
    import vectorbt as vbt

    primary=source if repaired else source/'pure_q1_comparison'
    names=['Pure_RRG_Q1','Report_base','Industry_momentum_220_6','ETF_equal_weight']
    base_weights={n:pd.read_parquet(primary/f'{n}_weights.parquet') for n in names}
    cols=sorted(set().union(*(set(w.columns) for w in base_weights.values())))
    dates=base_weights[names[0]].index
    exposures=build_exposure_panel(pd.read_parquet(source/'candidate_etf_panel.parquet'),
                                  pd.read_parquet(source/'visible_etf_industry_weights.parquet'))
    assert set(dates).issubset(exposures)
    meta=fetch_etf_meta(only_listed=not repaired,drop_qdii=True)
    meta=meta.loc[meta.list_date.le(pd.Timestamp(END))]
    close,open_,_=fetch_etf_daily(cols,START,END,lookback=25)
    close,open_=close[cols].loc[START:],open_[cols].loc[START:]
    plans=[]
    fingerprints={}
    if repaired:
        # Price-based signals depend on these unchanged tables, not on ETF pool.
        # Verify equality before reusing the already computed 27 signal matrices.
        for path,label in [(DB,'prior'),(database,'repaired')]:
            with duckdb.connect(str(path),read_only=True) as con:
                fingerprints[label]={t:con.execute(f'SELECT count(*),sum(hash(t)) FROM {t} t').fetchone()
                    for t in ['ashares_close','ashares_float_mv','zx_membership','zx_industry_daily']}
        assert fingerprints['prior']==fingerprints['repaired']
        previous_run=json.loads((SOURCE/'four_strategy_robustness/run.json').read_text())
        assert previous_run['input_sha256']==sha(DB)
        from rrg_repair_backtest import settlement_prices
        meta_all=pd.read_parquet(source/'etf_metadata_audit.parquet').set_index('ts_code')
        close,open_,plans=settlement_prices(base_weights['ETF_equal_weight'].reindex(columns=cols).fillna(0.),
            close,open_,meta_all,save_plan_to=target/'approved_nav_settlement_plan.csv')

    def check_portfolio(pf,w,fee):
        result=validate(pf,w,close,fee)
        if plans:
            orders=pf.orders.records_readable
            signed=orders.Size.where(orders.Side.eq('Buy'),-orders.Size)
            daily=orders.assign(signed=signed).pivot_table(index='Timestamp',columns='Column',values='signed',aggfunc='sum')
            shares=daily.reindex(index=close.index,columns=cols).fillna(0.).cumsum()
            for plan in plans:
                assert shares.loc[plan['execution_date']:,plan['code']].abs().max()<1e-5
            for code in cols:
                retired=meta_all.loc[code,'delist_date']
                if pd.notna(retired): assert (w.index[w[code]>1e-8]<retired).all()
                assert (w.index[w[code]>1e-8]>=meta_all.loc[code,'list_date']).all()
            dates_by_code={p['code']:pd.Timestamp(p['execution_date']) for p in plans}
            estimates=orders.Side.eq('Sell') & orders.Timestamp.eq(orders.Column.map(dates_by_code))
            result.update(retired_positions_zero_after_settlement=True,
                          estimated_settlement_count=int(estimates.sum()))
        return result
    previous=vbt.settings.portfolio['call_seq']
    vbt.settings.portfolio['call_seq']='auto'
    costs,checks,base_navs=[],{},{}
    try:
        for bp in [3,5,10,20]:
            for name,w in base_weights.items():
                print(f'Cost {name}: {bp}bp',flush=True)
                w=w.reindex(index=dates,columns=cols).fillna(0.)
                pf=run_vbt_backtest(w,close,open_,fees=bp/10000,slippage=0.,init_cash=1e7)
                account=check_portfolio(pf,w,bp/10000)
                checks[f'{name}_{bp}bp']=account
                if bp==3:
                    prior=pd.read_csv(primary/f'{name}_nav.csv',index_col=0).iloc[:,0]
                    np.testing.assert_allclose(pf.value().to_numpy(),prior.to_numpy(),rtol=1e-12)
                    base_navs[name]=pf.value()
                row=dict(portfolio_stats(pf),strategy=name,one_way_bp=bp,**account)
                costs.append(row)
                pf.value().to_csv(target/f'{name}_{bp}bp_nav.csv')
                pf.orders.records_readable.to_csv(target/f'{name}_{bp}bp_orders.csv',index=False)
        cost=pd.DataFrame(costs)
        cost.to_csv(target/'cost_sensitivity.csv',index=False)
        cost.pivot(index='one_way_bp',columns='strategy',values='年化收益率').to_csv(target/'cost_cagr.csv')
        navs=pd.DataFrame(base_navs)
        month_end=navs.resample('ME').last()
        monthly=month_end.pct_change(fill_method=None)
        monthly.iloc[0]=month_end.iloc[0]/1e7-1
        monthly.to_csv(target/'monthly_returns_all.csv')
        complete=monthly.loc['2019-01-01':]
        assert len(complete)==87 and complete.notna().all().all()
        complete.to_csv(target/'monthly_returns_complete.csv')
        annual=(1+monthly).groupby(monthly.index.year).prod()-1
        annual.to_csv(target/'annual_returns.csv')
        for year in annual.index:
            part=navs.loc[navs.index.year==year]
            prior=navs.loc[navs.index<part.index[0]]
            before=prior.iloc[-1] if len(prior) else 1e7
            np.testing.assert_allclose(annual.loc[year],part.iloc[-1]/before-1,atol=1e-10)
        rolling=(1+complete).rolling(12,min_periods=12).apply(np.prod,raw=True).sub(1).dropna()
        ranks=rolling.rank(axis=1,ascending=False,method='min')
        rolling.to_csv(target/'rolling_12m_returns.csv')
        ranks.to_csv(target/'rolling_12m_ranks.csv')
        pd.DataFrame({'windows':len(rolling),'top_windows':ranks.eq(1).sum(),
            'positive_windows':rolling.gt(0).sum(),'median_12m':rolling.median(),
            'worst_12m':rolling.min(),'best_12m':rolling.max()}).to_csv(target/'rolling_summary.csv')
        dependency=[]
        for name in names:
            m=complete[name];log=np.log1p(m)
            original=float((navs[name].iloc[-1]/1e7)**(252/len(navs))-1)
            row={'strategy':name,'cagr':original,'positive_months':int(m.gt(0).sum()),
                'negative_months':int(m.lt(0).sum()),'best_month':str(m.idxmax().date()),
                'best_month_return':float(m.max()),'worst_month':str(m.idxmin().date()),
                'worst_month_return':float(m.min()),'top3_share_log_growth':float(log.nlargest(3).sum()/log.sum())}
            for k in [1,3]:
                flat=monthly[name].copy()
                flat.loc[m.nlargest(k).index]=0.
                row[f'best{k}_flat_cagr']=float(np.prod(1+flat)**(252/len(navs))-1)
            dependency.append(row)
        pd.DataFrame(dependency).to_csv(target/'month_dependency.csv',index=False)

        # Prepare prices once. For 180/260, reuse the unmodified diffusion
        # arithmetic plus exactly the same mathematical zero correction as source.
        if not repaired:
            with duckdb.connect(str(database),read_only=True) as con:
                c=con.execute('SELECT * FROM ashares_close ORDER BY date,stock_code').df()
                f=con.execute('SELECT * FROM ashares_float_mv ORDER BY date,stock_code').df()
                m=con.execute('SELECT * FROM zx_membership ORDER BY trade_date,stock_code').df()
            stock_close=c.pivot(index='date',columns='stock_code',values='close')
            cap=f.pivot(index='date',columns='stock_code',values='float_mv').reindex(index=stock_close.index,columns=stock_close.columns)
            member=m.pivot(index='trade_date',columns='stock_code',values='industry_code').reindex(index=stock_close.index,columns=stock_close.columns)
            del c,f,m;gc.collect()
            benchmark,industry_close,_=fetch_rrg_dataset(start_date=WARMUP,end_date=END,classification='zx')
            industry_close=industry_close.drop(columns=EXCLUDE)
        grid_monthly={}
        grid_records=[]
        for lookback in [180,220,260]:
            print(f'Preparing {lookback}-day signals',flush=True)
            if repaired:
                pass  # Checked unchanged signal inputs; load exact saved matrices below.
            elif lookback==220:
                ratio=pd.read_parquet(PARENT/'rs_ratio.parquet')
                mom=pd.read_parquet(PARENT/'rs_momentum.parquet')
                breadth=pd.read_parquet(SOURCE/'free_float_diffusion.parquet')
            else:
                ratio,mom=compute_rrg(industry_close,benchmark,lookback,60,20)
                raw=diffusion_value_weighted(stock_close,member,cap,lookback=lookback,smooth_window=1)
                flag=_up_flags(stock_close,lookback)
                corrected=0
                for industry in raw.columns:
                    valid=member.eq(industry)&flag.notna()&cap.gt(0)
                    zero=valid.any(axis=1)&(valid&flag.eq(1)).sum(axis=1).eq(0)
                    assert raw.loc[zero,industry].isna().all()
                    corrected+=int(zero.sum())
                    raw.loc[zero,industry]=0.
                breadth=raw.rolling(20).mean()
                breadth=breadth.drop(columns=EXCLUDE,errors='ignore')
                breadth.to_parquet(target/f'diffusion_{lookback}.parquet')
                print(f'{lookback} breadth complete, corrected {corrected} zero-industry days',flush=True)
                del raw,flag,valid,zero;gc.collect()
            if not repaired:
                assert ratio.loc[dates].notna().all().all() and mom.loc[dates].notna().all().all()
            for n in [4,6,8]:
                if repaired:
                    score_sets={name:pd.read_parquet(SOURCE/'four_strategy_robustness'/f'{name}_{lookback}_{n}_signals.parquet')
                                for name in names[:3]}
                    if lookback==220 and n==6:
                        for name,scores in score_sets.items():
                            pd.testing.assert_frame_equal(scores,pd.read_parquet(source/f'{name}_signals.parquet'),check_names=False,check_freq=False)
                else:
                    score_sets={
                        'Pure_RRG_Q1':select_by_quadrant(ratio,mom,quadrants=(1,),top_n=n).loc[dates].astype(float),
                        'Report_base':select_diffusion_with_rrg(breadth.reindex(index=ratio.index,columns=ratio.columns),ratio,mom,top_n=n,keep_quadrants=(1,2)).loc[dates].astype(float),
                        'Industry_momentum_220_6':select_by_diffusion(industry_close.pct_change(lookback,fill_method=None),top_n=n).loc[dates].astype(float)}
                for name,scores in score_sets.items():
                    label=f'{name}_{lookback}_{n}'
                    print('Grid '+label,flush=True)
                    w=mask_listed(batch_optimize(scores,exposures=exposures,dates=dates,preset='base',strict=True),meta)
                    assert set(w.columns).issubset(cols)
                    w=w.reindex(index=dates,columns=cols).fillna(0.)
                    pf=run_vbt_backtest(w,close,open_,fees=.0003,slippage=0.,init_cash=1e7)
                    check=check_portfolio(pf,w,.0003)
                    checks[label]=check
                    if lookback==220 and n==6:
                        np.testing.assert_allclose(pf.value().to_numpy(),base_navs[name].to_numpy(),rtol=1e-12)
                    q=pf.value().resample('ME').last().pct_change(fill_method=None).loc['2019-01-01':]
                    assert len(q)==87 and q.notna().all()
                    grid_monthly[label]=q
                    record=dict(portfolio_stats(pf),strategy=name,lookback=lookback,max_industries=n,
                        avg_selected_industries=float(scores.iloc[:-1].sum(axis=1).mean()),**check)
                    grid_records.append(record)
                    scores.to_parquet(target/f'{label}_signals.parquet')
                    w.to_parquet(target/f'{label}_weights.parquet')
                    pf.value().to_csv(target/f'{label}_nav.csv')
                    pd.DataFrame(grid_records).to_csv(target/'parameter_grid.csv',index=False)
            if not repaired:
                del breadth;gc.collect()
        pd.DataFrame(grid_monthly).to_csv(target/'grid_monthly_returns.csv')
    finally:
        vbt.settings.portfolio['call_seq']=previous
    write_json(target/'run.json',dict(run,
        task='Unified time, cost, grid and inference inputs for four report-period ETF portfolios',
        primary_strategies=names,friction_grid_bp=[3,5,10,20],lookback_grid=[180,220,260],max_industry_grid=[4,6,8],
        parameter_definitions={'Q1':'RS-ratio lookback varies; RS-momentum60/MA20 fixed; at most N first-quadrant industries.',
            'combined':'Diffusion and RS-ratio share lookback; top N diffusion then keep Q1/Q2; no refill.',
            'momentum':'Industry cumulative return over lookback; top N.',
            'equal_weight':'Fixed same ETF candidate pool; no analogous signal parameter.'},
        rolling_and_inference_months='2019-01 through 2026-03; exclude the partial initial Dec2018; 87 complete months.',
        full_sample_trading_days=len(close),annualization=252,
        inference_plan={'HAC_primary_lags':3,'HAC_sensitivity_lags':[1,3,6],'bootstrap_blocks':[3,6],
            'bootstrap_reps':10000,'seed':20261107,'primary_Holm_family':'All six unordered pairs of four strategies.',
            'grid_Holm_family':'Within each of nine settings: all three active pairs and each active versus fixed equal-weight (54 comparisons).'},
        cost_scope='Illustrative total one-way friction charged as fees; no additional slippage or calibrated impact.',
        signal_reuse_verified_tables=fingerprints,
        retirement_approximation_plans=len(plans),
        source_environment={'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__},
        disclosure='Same already-inspected historical sample; no new out-of-sample claim. All input coverage deviations inherited.'))
    write_json(target/'validation.json',dict(accounting=checks,primary_base_navs_replayed_exactly=True,
        all_three_220_6_grid_navs_match_primary=True,annual_returns_reconcile=True,
        complete_months=87,rolling_windows=len(rolling),grid_cases=len(grid_records),cost_cases=len(costs),
        upstream_commit=verify_upstream(),input_sha256=run['input_sha256']))
    print('COMPLETE: 16 cost scenarios, 27 grid cases, time analyses and inference inputs',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repaired',action='store_true',help='Use the repaired historical ETF pool and approved NAV settlement approximation.')
    main(parser.parse_args().repaired)
