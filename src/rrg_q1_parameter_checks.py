"""Prespecified pure first-quadrant RRG grid against fixed and matched baselines."""
import hashlib
import json

import numpy as np
import pandas as pd

from rrg_pipeline import OUT, SIGNAL_START, BACKTEST_START, END, EXCLUDE, bind_upstream, verify_upstream, write_json
from run_rrg_baselines import validate


def main():
    source=OUT/'author_baseline_comparison'
    target=source/'ablation/q1_parameter_checks'
    target.mkdir(parents=True,exist_ok=True)
    run=json.loads((source/'sell_first_run.json').read_text())
    with open(run['input_database'],'rb') as f:
        assert hashlib.file_digest(f,'sha256').hexdigest()==run['input_database_sha256']
    assert verify_upstream()==run['upstream_commit']
    reference=source/'data_grid_risk_checks'
    reference_run=json.loads((reference/'run.json').read_text())
    assert reference_run['input_database_sha256']==run['input_database_sha256']
    reference_grid=pd.read_csv(reference/'parameter_grid.csv')
    matched=reference_grid.loc[reference_grid.strategy.eq('Industry_momentum')].set_index(['lookback','top_n'])
    fixed=pd.read_csv(source/'sell_first_performance.csv',index_col=0)
    fixed_momentum=float(fixed.loc['年化收益率','Industry_momentum_220_6'])
    fixed_equal=float(fixed.loc['年化收益率','ETF_equal_weight'])
    bind_upstream(run['input_database'])
    from src.data_provider import fetch_etf_meta,fetch_etf_daily
    from src.exposure import ETFExposureBuilder
    from src.store import RRGStore
    from src.backtest import build_exposure_panel,mask_listed,run_vbt_backtest,portfolio_stats
    from src.generator import RRGSignalGenerator
    from src.optimizer import batch_optimize
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
    rows,checks=[],{}
    previous=vbt.settings.portfolio['call_seq']
    vbt.settings.portfolio['call_seq']='auto'
    try:
        for lookback in [180,220,260]:
            # Pure quadrant uses only industry index history: no stock-level
            # diffusion prefetch, so all lookbacks can start in Jan 2021.
            gen=RRGSignalGenerator(start_date=SIGNAL_START,end_date=END,classification='zx',
                lookback_ratio=lookback,lookback_mom=60,smooth_window=20)
            for top_n in [4,6,8]:
                label=f'Q1_{lookback}_{top_n}'
                print('Running '+label,flush=True)
                scores=gen.generate('quadrant',quadrants=(1,),top_n=top_n,exclude=EXCLUDE).reindex(dates).fillna(False).astype(float)
                # Check complete warm-up at the first implemented formation.
                ratio,momentum=gen.get_rrg()
                first=dates[dates>=pd.Timestamp(BACKTEST_START)][0]
                assert ratio.loc[first].drop(labels=EXCLUDE,errors='ignore').notna().all()
                assert momentum.loc[first].drop(labels=EXCLUDE,errors='ignore').notna().all()
                assert scores.sum(axis=1).le(top_n).all()
                weights=mask_listed(batch_optimize(scores,exposures=exposures,dates=dates,preset='base',strict=True),meta)
                assert set(weights.columns).issubset(cols)
                weights=weights.reindex(index=dates,columns=cols).fillna(0.)
                pf=run_vbt_backtest(weights.loc[BACKTEST_START:],close,open_,fees=.0003,slippage=0.,init_cash=1e7)
                checks[label]=validate(pf,weights,close,run)
                if lookback==220 and top_n==6:
                    old_weights=pd.read_parquet(source/'ablation/RRG_Q1_only_weights.parquet')
                    np.testing.assert_allclose(weights.to_numpy(),old_weights.to_numpy(),atol=1e-12)
                    old_nav=pd.read_csv(source/'ablation/RRG_Q1_only_nav.csv',index_col=0).iloc[:,0]
                    np.testing.assert_allclose(pf.value().to_numpy(),old_nav.to_numpy(),rtol=1e-12)
                # Verify the previously computed matched momentum's entire NAV.
                mw=pd.read_parquet(reference/f'Industry_momentum_{lookback}_{top_n}_weights.parquet')
                mpf=run_vbt_backtest(mw.loc[BACKTEST_START:],close,open_,fees=.0003,slippage=0.,init_cash=1e7)
                old_momentum=pd.read_csv(reference/f'Industry_momentum_{lookback}_{top_n}_nav.csv',index_col=0).iloc[:,0]
                np.testing.assert_allclose(mpf.value().to_numpy(),old_momentum.to_numpy(),rtol=1e-12)
                np.testing.assert_allclose(mpf.annualized_return(),matched.loc[(lookback,top_n),'年化收益率'],atol=1e-12)
                result=portfolio_stats(pf).to_dict()
                annual=result['年化收益率']
                observed=scores.loc[BACKTEST_START:].iloc[:-1].sum(axis=1)
                result.update(lookback=lookback,max_industries=top_n,
                    profit_cny=checks[label]['profit_cny'],fees_cny=checks[label]['fees_cny'],
                    avg_selected_industries=float(observed.mean()),zero_industry_formations=int(observed.eq(0).sum()),
                    mean_cash_fraction=float((pf.cash()/pf.value()).mean()),
                    fixed_momentum_cagr=fixed_momentum,equal_weight_cagr=fixed_equal,
                    matched_momentum_cagr=float(matched.loc[(lookback,top_n),'年化收益率']),
                    beats_fixed_momentum=bool(annual>fixed_momentum),beats_equal_weight=bool(annual>fixed_equal),
                    beats_both_fixed=bool(annual>max(fixed_momentum,fixed_equal)),
                    beats_matched_momentum=bool(annual>matched.loc[(lookback,top_n),'年化收益率']))
                rows.append(result)
                scores.to_parquet(target/f'{label}_signals.parquet')
                weights.to_parquet(target/f'{label}_weights.parquet')
                pf.value().to_csv(target/f'{label}_nav.csv')
                pf.orders.records_readable.to_csv(target/f'{label}_orders.csv',index=False)
                pd.DataFrame(rows).to_csv(target/'parameter_grid.csv',index=False)
    finally:
        vbt.settings.portfolio['call_seq']=previous
    df=pd.DataFrame(rows)
    summary={'settings':len(df),'beats_fixed_momentum':int(df.beats_fixed_momentum.sum()),
        'beats_equal_weight':int(df.beats_equal_weight.sum()),'beats_both_fixed':int(df.beats_both_fixed.sum()),
        'beats_matched_momentum':int(df.beats_matched_momentum.sum()),
        'cagr_min':float(df['年化收益率'].min()),'cagr_median':float(df['年化收益率'].median()),
        'cagr_max':float(df['年化收益率'].max()),'fixed_momentum_cagr':fixed_momentum,'equal_weight_cagr':fixed_equal}
    write_json(target/'summary.json',summary)
    write_json(target/'validation.json',dict(accounting=checks,base_q1_replay_exact=True,
        all_matched_momentum_navs_replayed_exactly=True,full_warmup_before_first_formation=True,
        upstream_commit=verify_upstream(),input_database_sha256=run['input_database_sha256']))
    write_json(target/'run.json',dict(run,tested_strategy='pure RRG first quadrant',lookback_grid=[180,220,260],
        max_industry_grid=[4,6,8],signal_start=SIGNAL_START,lookback_momentum=60,smoothing=20,
        signal_definition='First quadrant only; distance from RRG center descending; at most N industries. No diffusion input.',
        fixed_comparison='Momentum stays 220 days/top 6; ETF equal-weight unchanged.',
        matched_comparison='Momentum uses same nominal lookback and top N; Q1 can select fewer industries.',
        purpose='Prespecified grid on an already inspected sample; report all settings, retain original 220/6 reference.'))
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4),sharey=True)
    a=df.pivot(index='lookback',columns='max_industries',values='年化收益率')*100
    delta=df.assign(gap=(df['年化收益率']-df.matched_momentum_cagr)*100).pivot(index='lookback',columns='max_industries',values='gap')
    for ax,values,title,cmap in [(axes[0],a,'Pure RRG Q1: net CAGR (%)','YlGnBu'),
        (axes[1],delta,'Q1 minus matched momentum (pp)','RdYlGn')]:
        if ax is axes[1]:
            bound=float(np.abs(values.to_numpy()).max())
            ax.imshow(values,cmap=cmap,aspect='auto',vmin=-bound,vmax=bound)
        else:
            ax.imshow(values,cmap=cmap,aspect='auto')
        ax.set(title=title,xlabel='Maximum industry count',xticks=range(3),xticklabels=values.columns,
               yticks=range(3),yticklabels=values.index)
        for i in range(3):
            for j in range(3):
                ax.text(j,i,f'{values.iloc[i,j]:.2f}',ha='center',va='center',color='black',
                    bbox=dict(facecolor='white',alpha=.65,edgecolor='none',pad=2))
    axes[0].set_ylabel('RS-ratio lookback (trading days)')
    fig.suptitle('Same data and execution | 3bp per side')
    fig.tight_layout()
    fig.savefig(target/'q1_parameter_grid.png',dpi=160)
    print(df[['lookback','max_industries','年化收益率','最大回撤','matched_momentum_cagr','beats_both_fixed']].to_string(index=False))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
