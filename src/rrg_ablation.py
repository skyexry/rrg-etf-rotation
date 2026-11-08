"""Signal and allocation ablations using the original RRG implementation."""
import hashlib
import json

import numpy as np
import pandas as pd

from rrg_pipeline import OUT, SIGNAL_START, BACKTEST_START, END, EXCLUDE, bind_upstream, verify_upstream, write_json
from run_rrg_baselines import validate


def main():
    source=OUT/'author_baseline_comparison'
    target=source/'ablation'
    target.mkdir(parents=True,exist_ok=True)
    run=json.loads((source/'sell_first_run.json').read_text())
    with open(run['input_database'],'rb') as f:
        assert hashlib.file_digest(f,'sha256').hexdigest()==run['input_database_sha256']
    bind_upstream(run['input_database'])
    from src.generator import RRGSignalGenerator
    from src.data_provider import fetch_etf_meta,fetch_etf_daily
    from src.exposure import ETFExposureBuilder
    from src.store import RRGStore
    from src.backtest import build_exposure_panel,mask_listed,run_vbt_backtest,portfolio_stats
    from src.optimizer import batch_optimize,optimize_etf_weights
    from src.strategy import select_by_diffusion
    import vectorbt as vbt

    original=pd.read_parquet(source/'RRG_weights.parquet')
    dates,cols=original.index,list(original.columns)
    meta=fetch_etf_meta(only_listed=True,drop_qdii=True)
    with RRGStore(run['input_database']) as store:
        with ETFExposureBuilder(sorted(meta.index),SIGNAL_START,END,store=store,classification='zx') as builder:
            iw=builder.build_industry_weights()
    exposures=build_exposure_panel(pd.read_parquet(OUT/'author_snapshot_replay/candidate_etf_panel.parquet'),iw)
    close,open_,_=fetch_etf_daily(cols,SIGNAL_START,END,lookback=25)
    close,open_=close[cols].loc[BACKTEST_START:],open_[cols].loc[BACKTEST_START:]
    print('Computing original 220/60/20 signal components',flush=True)
    gen=RRGSignalGenerator(start_date=SIGNAL_START,end_date=END,classification='zx')
    full=gen.generate('diffusion_rrg',top_n=6,keep_quadrants=(1,2),exclude=EXCLUDE).reindex(dates).fillna(False).astype(float)
    diffusion=gen.generate('diffusion',top_n=6,exclude=EXCLUDE).reindex(dates).fillna(False).astype(float)
    q1=gen.generate('quadrant',top_n=6,quadrants=(1,),exclude=EXCLUDE).reindex(dates).fillna(False).astype(float)
    q12=gen.generate('quadrant',top_n=6,quadrants=(1,2),exclude=EXCLUDE).reindex(dates).fillna(False).astype(float)
    old=pd.read_parquet(OUT/'author_snapshot_replay/industry_selection.parquet')
    old.index=old.index.as_unit('ns')
    full.index=full.index.as_unit('ns')
    pd.testing.assert_frame_equal(full,old)
    # Keep exactly the full signal's k_t and therefore its industry cap 1/k_t.
    # Ranking remains the original pure diffusion ranking: no quadrant filter.
    diff_values=gen._diffusion.drop(columns=EXCLUDE,errors='ignore').reindex(dates)
    matched=pd.DataFrame({d:select_by_diffusion(diff_values.loc[[d]],top_n=int(full.loc[d].sum())).iloc[0]
                          for d in dates}).T.astype(float)
    np.testing.assert_array_equal(matched.sum(axis=1),full.sum(axis=1))
    cases=[('Full_base',full,'base','Original combined signal and base allocation'),
        ('Diffusion_only',diffusion,'base','Remove RRG filter; top six diffusion industries'),
        ('Diffusion_matched_k',matched,'base','Remove RRG; match full signal industry count and caps'),
        ('RRG_Q1_only',q1,'base','Remove diffusion; first quadrant, top six by distance'),
        ('RRG_Q12_only',q12,'base','Remove diffusion; quadrants one/two, top six by distance'),
        ('Full_report_cap20_cash',full,'etf_capped','Report section 3.2: ETF <=20%, gross=min(1,.2*k)'),
        ('Full_report_no_industry_cap',full,'unconstrained','Report section 3.3: no industry cap, gross<=1'),
        ('Full_cap20_only',full,'cap_only','Our extension: ETF <=20%, retain full gross and industry cap'),
        ('Full_cash_rule_only',full,'cash_only','Our extension: reduced gross rule, retain ETF upper bound one')]
    stats,checks=[],{}
    previous=vbt.settings.portfolio['call_seq']
    vbt.settings.portfolio['call_seq']='auto'
    try:
        for name,scores,preset,description in cases:
            print('Running '+name,flush=True)
            if preset in ['cap_only','cash_only']:
                rows={}
                for d in dates:
                    k=int(scores.loc[d].sum())
                    rows[d]=optimize_etf_weights(scores.loc[d],exposures[d],
                        w_max=.2 if preset=='cap_only' else 1.,
                        gross_target=1. if preset=='cap_only' else min(1.,.2*k),
                        gross_mode='eq',industry_cap=1/k if k else None)
                weights=pd.DataFrame(rows).T
            else:
                weights=batch_optimize(scores,exposures=exposures,dates=dates,preset=preset,strict=True)
            weights=mask_listed(weights,meta)
            assert set(weights.columns).issubset(cols)
            weights=weights.reindex(index=dates,columns=cols).fillna(0.)
            pf=run_vbt_backtest(weights.loc[BACKTEST_START:],close,open_,fees=.0003,slippage=0.,init_cash=1e7)
            checks[name]=validate(pf,weights,close,run)
            if name=='Full_base':
                np.testing.assert_allclose(weights.to_numpy(),original.to_numpy(),atol=1e-12)
                nav=pd.read_csv(source/'RRG_sell_first_nav.csv',index_col=0).iloc[:,0]
                np.testing.assert_allclose(pf.value().to_numpy(),nav.to_numpy(),rtol=1e-12)
            result=portfolio_stats(pf).to_dict()
            result.update(case=name,description=description,profit_cny=checks[name]['profit_cny'],
                fees_cny=checks[name]['fees_cny'],mean_cash_fraction=float((pf.cash()/pf.value()).mean()),
                avg_selected_industries=float(scores.loc[BACKTEST_START:].iloc[:-1].sum(axis=1).mean()),
                avg_largest_target_etf=float(weights.loc[BACKTEST_START:].iloc[:-1].max(axis=1).mean()))
            stats.append(result)
            scores.to_parquet(target/f'{name}_signals.parquet')
            weights.to_parquet(target/f'{name}_weights.parquet')
            pf.value().to_csv(target/f'{name}_nav.csv')
            pf.orders.records_readable.to_csv(target/f'{name}_orders.csv',index=False)
            pd.DataFrame(stats).to_csv(target/'performance.csv',index=False)
    finally:
        vbt.settings.portfolio['call_seq']=previous
    write_json(target/'validation.json',dict(accounting=checks,base_replay_exact=True,
        matched_industry_count_exact=True,upstream_commit=verify_upstream(),input_sha256=run['input_database_sha256']))
    write_json(target/'run.json',dict(run,cases=[{'name':n,'preset':p,'description':d} for n,s,p,d in cases],
        no_retuning=True,signal_ablation_scope='ETF implementation of signal contrasts; report original signal comparisons are industry-index portfolios.',
        constraints_note='Report ETF-capped case changes both per-ETF cap and gross. Our two one-change cases separate them.',
        industry_count_control='Diffusion_matched_k uses the combined strategy monthly count, keeping 1/k caps equal.'))
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,5))
    df=pd.DataFrame(stats).set_index('case')
    df['年化收益率'].mul(100).plot.barh(ax=axes[0],color='#327c9d')
    df['最大回撤'].mul(-100).plot.barh(ax=axes[1],color='#c47a43')
    axes[0].set(xlabel='Net CAGR (%)',ylabel='')
    axes[1].set(xlabel='Maximum drawdown magnitude (%)',ylabel='')
    for ax in axes: ax.grid(axis='x',alpha=.25)
    fig.suptitle('Signal and allocation ablations | Same data, sell before buy, 3bp/side')
    fig.tight_layout()
    fig.savefig(target/'ablation.png',dpi=160)
    print(df[['年化收益率','最大回撤','夏普比率','mean_cash_fraction']].to_string(),flush=True)


if __name__=='__main__':
    main()
