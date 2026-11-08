"""Cross-snapshot checks, prespecified parameter grid, and concentration audit."""
import hashlib
import json
import argparse

import duckdb
import numpy as np
import pandas as pd

from rrg_pipeline import (OUT, DB, SIGNAL_START, BACKTEST_START, END, EXCLUDE,
                          bind_upstream, verify_upstream, write_json)
from run_rrg_baselines import industry_momentum, validate


def cross_snapshot(database, iw, target):
    from src.store import RRGStore
    from src.exposure import ETFExposureBuilder
    from src.backtest import _pit_latest_holdings
    frames, holdings = {}, {}
    for label, path in [('author', database), ('downloaded', DB)]:
        with duckdb.connect(str(path), read_only=True) as c:
            frames[label] = c.execute("""SELECT date,adjclose,adjopen FROM etf_daily
                WHERE code='159906.SZ' AND date BETWEEN '2026-05-29' AND '2026-08-31'
                ORDER BY date""").df().set_index('date')
        with RRGStore(path) as store:
            with ETFExposureBuilder(['159906.SZ'], '2026-06-01', END,
                                    store=store, classification='zx') as builder:
                derived = builder.build_industry_weights()
                raw = builder.load_portfolio().copy()
        snap = _pit_latest_holdings(derived, pd.Timestamp('2026-06-30'))
        snap.to_csv(target/f'{label}_159906_industry_mapping.csv', index=False)
        if not snap.empty:
            period = snap.end_date.max()
            holdings[label] = raw.loc[raw.end_date.eq(period)].copy()
            holdings[label].to_csv(target/f'{label}_159906_visible_holdings.csv', index=False)
    prices = frames['author'].join(frames['downloaded'], lsuffix='_author', rsuffix='_downloaded', how='outer')
    for label, f in frames.items():
        prices[f'return_{label}'] = f.adjclose.pct_change(fill_method=None)
    prices['return_difference'] = prices.return_author-prices.return_downloaded
    prices.to_csv(target/'159906_price_crosscheck.csv')
    common = prices.dropna()
    assert len(common) > 50
    mapping_a = pd.read_csv(target/'author_159906_industry_mapping.csv').set_index('sw_code').weight
    mapping_b = pd.read_csv(target/'downloaded_159906_industry_mapping.csv').set_index('sw_code').weight
    mapping = pd.concat([mapping_a.rename('author'), mapping_b.rename('downloaded')], axis=1).fillna(0.)
    mapping['difference'] = mapping.author-mapping.downloaded
    mapping.to_csv(target/'159906_industry_mapping_difference.csv')
    ha, hb = holdings['author'], holdings['downloaded']
    detail = ha[['symbol','stk_mkv_ratio']].merge(hb[['symbol','stk_mkv_ratio']], on='symbol', how='outer',
                                               suffixes=('_author','_downloaded'), indicator=True)
    detail.to_csv(target/'159906_stock_holdings_difference.csv', index=False)
    holding_diff = detail.stk_mkv_ratio_author-detail.stk_mkv_ratio_downloaded
    result = {'code': '159906.SZ', 'mapping_formation_date': '2026-06-30',
        'author_quote_days': len(frames['author']), 'downloaded_quote_days': len(frames['downloaded']),
        'common_return_days': len(common), 'max_abs_daily_return_difference': float(common.return_difference.abs().max()),
        'max_abs_close_level_difference': float((common.adjclose_author-common.adjclose_downloaded).abs().max()),
        'max_abs_open_level_difference': float((common.adjopen_author-common.adjopen_downloaded).abs().max()),
        'mapping_max_abs_weight_difference': float(mapping.difference.abs().max()),
        'author_holdings_period': str(ha.end_date.max().date()),
        'downloaded_holdings_period': str(hb.end_date.max().date()),
        'author_holdings_count': len(ha), 'downloaded_holdings_count': len(hb),
        'unmatched_stock_rows': int(detail['_merge'].ne('both').sum()),
        'max_abs_stock_nav_weight_difference_pp': float(holding_diff.abs().max()),
        'all_visible_holdings_fields_equal': bool(ha.sort_values('symbol').reset_index(drop=True).equals(hb.sort_values('symbol').reset_index(drop=True))),
        'author_effective_announcement': str(ha.ann_date.max().date()),
        'downloaded_effective_announcement': str(hb.ann_date.max().date()),
        'scope': 'Cross-snapshot agreement, not independent-vendor validation; both may use Tushare upstream.'}
    write_json(target/'data_crosscheck.json', result)
    print('CROSSCHECK '+json.dumps(result, ensure_ascii=False), flush=True)


def risk_checks(source, target, exposures, iw, close, open_, meta):
    from src.backtest import _pit_latest_holdings
    names = ['RRG', 'Industry_momentum_220_6', 'ETF_equal_weight']
    signals = {'RRG':pd.read_parquet(OUT/'author_snapshot_replay/industry_selection.parquet'),
               'Industry_momentum_220_6':pd.read_parquet(source/'industry_momentum_selection.parquet')}
    summaries, monthly = [], []
    for name in names:
        w = pd.read_parquet(source/f'{name}_weights.parquet')
        orders = pd.read_csv(source/f'{name}_sell_first_orders.csv', parse_dates=['Timestamp'])
        nav = pd.read_csv(source/f'{name}_sell_first_nav.csv', index_col=0, parse_dates=True).iloc[:,0]
        signed = orders.Size.where(orders.Side.eq('Buy'), -orders.Size)
        trades = orders.assign(signed=signed).pivot_table(index='Timestamp', columns='Column', values='signed', aggfunc='sum')
        q = trades.reindex(index=nav.index,columns=w.columns).fillna(0.).cumsum()
        p = close[w.columns].reindex(nav.index).ffill()
        cashflows = (-signed*orders.Price-orders.Fees).groupby(orders.Timestamp).sum()
        cash = 1e7+cashflows.reindex(nav.index,fill_value=0.).cumsum()
        np.testing.assert_allclose(cash+(q*p).fillna(0.).sum(axis=1), nav, atol=1e-5)
        actual = (q*p).fillna(0.).div(nav,axis=0)
        invested = actual.sum(axis=1)
        normalized = actual.div(invested.where(invested>1e-8),axis=0)
        metrics = pd.DataFrame({'largest_etf_weight': actual.max(axis=1),
            'top3_etf_weight': pd.DataFrame(np.sort(actual.to_numpy(),axis=1)[:,-3:],index=actual.index).sum(axis=1),
            'conditional_etf_hhi': (normalized**2).sum(axis=1).where(invested>1e-8),
            'cash_fraction':cash/nav})
        metrics['effective_etf_count'] = 1/metrics.conditional_etf_hhi
        metrics.to_csv(target/f'{name}_daily_concentration.csv')
        active = metrics.loc[invested>1e-8]
        perf = pd.read_csv(source/'sell_first_performance.csv',index_col=0)[name]
        summaries.append({'strategy':name,'avg_largest_etf_weight':active.largest_etf_weight.mean(),
            'max_largest_etf_weight':active.largest_etf_weight.max(),
            'avg_top3_etf_weight':active.top3_etf_weight.mean(),
            'avg_effective_etf_count':active.effective_etf_count.mean(),
            'annual_volatility':perf['年化波动率'],'max_drawdown':perf['最大回撤']})
        for d in w.index[w.index>=pd.Timestamp(BACKTEST_START)]:
            pos = nav.index.get_indexer([d])[0]
            if pos < 0 or pos+1 >= len(nav):
                continue
            execute = nav.index[pos+1]
            E = exposures[d]
            tw = w.loc[d].reindex(E.index).fillna(0.)
            # Common open mark immediately after the scheduled rebalance.
            mark = open_.loc[execute].where(open_.loc[execute].gt(0)).combine_first(close.loc[d])
            av = (q.loc[execute]*mark.reindex(q.columns)).fillna(0.)
            aw = av/(cash.loc[execute]+av.sum())
            known_target = tw @ E
            known_actual = aw.reindex(E.index).fillna(0.) @ E
            k = int(signals[name].loc[d].sum()) if name in signals else None
            selected = signals[name].loc[d].reindex(E.columns).fillna(0.).gt(0) if name in signals else pd.Series(False,index=E.columns)
            max_selected = float(known_target[selected].max()) if selected.any() else 0.
            cap = 1/k if k else np.nan
            if k:
                assert max_selected <= cap+1e-5
            monthly.append({'strategy':name,'formation_date':d,'execution_date':execute,
                'target_largest_etf':tw.max(),'actual_largest_etf':aw.max(),
                'known_target_industry_coverage':known_target.sum(),
                'known_actual_industry_coverage':known_actual.sum(),
                'largest_known_target_industry':known_target.max(),
                'largest_known_actual_industry':known_actual.max(),
                'selected_industries':k,'selected_industry_cap':cap,
                'max_selected_industry_target_weight':max_selected})
    pd.DataFrame(summaries).to_csv(target/'risk_summary.csv',index=False)
    pd.DataFrame(monthly).to_csv(target/'monthly_industry_coverage.csv',index=False)
    d = pd.Timestamp('2026-06-30')
    w = pd.read_parquet(source/'RRG_weights.parquet').loc[d]
    E = exposures[d]
    selection = signals['RRG'].loc[d].reindex(E.columns).fillna(0.).gt(0)
    case = pd.DataFrame({'etf_nav_weight':E.loc['159906.SZ'],
        'portfolio_weight_from_etf':E.loc['159906.SZ']*w['159906.SZ'],
        'selected_industry':selection})
    case.to_csv(target/'159906_june2026_constraint_case.csv')
    pit = _pit_latest_holdings(iw, d)
    used = pit.loc[pit.code.eq('159906.SZ')]
    write_json(target/'concentration_case.json',{'date':str(d.date()), 'etf':'159906.SZ',
        'target_etf_weight':float(w['159906.SZ']), 'known_etf_industry_coverage':float(E.loc['159906.SZ'].sum()),
        'selected_industries':int(selection.sum()), 'selected_industry_cap':1/int(selection.sum()),
        'max_known_selected_portfolio_industry':float((w.reindex(E.index).fillna(0.) @ E)[selection].max()),
        'used_period':str(used.end_date.max().date()), 'used_effective_announcement':str(used.ann_date.max().date()),
        'interpretation':'ETF weight is not industry exposure. Missing disclosed coverage is unobserved, not zero real exposure. No per-ETF concentration cap in base preset.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-completed',action='store_true',help='Replay saved grid weights and verify their saved NAVs before continuing')
    args = parser.parse_args()
    source = OUT/'author_baseline_comparison'
    target = source/'data_grid_risk_checks'
    target.mkdir(parents=True, exist_ok=True)
    run = json.loads((source/'sell_first_run.json').read_text())
    with open(run['input_database'],'rb') as f:
        assert hashlib.file_digest(f,'sha256').hexdigest()==run['input_database_sha256']
    bind_upstream(run['input_database'])
    from src.data_provider import fetch_etf_meta, fetch_etf_daily, fetch_rrg_dataset
    from src.store import RRGStore
    from src.exposure import ETFExposureBuilder
    from src.backtest import build_exposure_panel, mask_listed, run_vbt_backtest, portfolio_stats
    from src.generator import RRGSignalGenerator
    from src.optimizer import batch_optimize
    import vectorbt as vbt
    meta = fetch_etf_meta(only_listed=True,drop_qdii=True)
    with RRGStore(run['input_database']) as store:
        with ETFExposureBuilder(sorted(meta.index),SIGNAL_START,END,store=store,classification='zx') as builder:
            iw = builder.build_industry_weights()
    panel = pd.read_parquet(OUT/'author_snapshot_replay/candidate_etf_panel.parquet')
    exposures = build_exposure_panel(panel,iw)
    dates = pd.read_parquet(source/'RRG_weights.parquet').index
    cols = list(pd.read_parquet(source/'RRG_weights.parquet').columns)
    close, open_, _ = fetch_etf_daily(cols,SIGNAL_START,END,lookback=25)
    close, open_ = close[cols],open_[cols]
    cross_snapshot(run['input_database'], iw, target)
    risk_checks(source,target,exposures,iw,close,open_,meta)
    _, industry_close,_ = fetch_rrg_dataset(start_date=SIGNAL_START,end_date=END,classification='zx')
    industry_close = industry_close.reindex(close.index).ffill()
    cached = set()
    if args.resume_completed and (target/'parameter_grid.csv').exists():
        previous = pd.read_csv(target/'parameter_grid.csv')
        cached = set(zip(previous.strategy,previous.lookback,previous.top_n))
    rows, checks = [], {}
    original_seq = vbt.settings.portfolio['call_seq']
    vbt.settings.portfolio['call_seq']='auto'
    try:
        for lookback in [180,220,260]:
            # Actual snapshot membership coverage starts 2020-01-07. March 5
            # is the earliest start with 280 prior sessions inside that waterline.
            # This still leaves 365 sessions before Aug 2022 (>358 warm-up).
            signal_start = '2021-03-05' if lookback==260 else SIGNAL_START
            complete = all((name,lookback,n) in cached for name in ['RRG','Industry_momentum'] for n in [4,6,8])
            gen = None if complete else RRGSignalGenerator(start_date=signal_start,end_date=END,
                classification='zx',lookback_ratio=lookback,lookback_mom=60,smooth_window=20)
            for top_n in [4,6,8]:
                if complete:
                    rrg_scores = mom_scores = None
                else:
                    rrg_scores = gen.generate('diffusion_rrg',top_n=top_n,keep_quadrants=(1,2),exclude=EXCLUDE).reindex(dates).fillna(False).astype(float)
                    _, mom_scores = industry_momentum(industry_close,dates,lookback,top_n)
                for name, scores in [('RRG',rrg_scores),('Industry_momentum',mom_scores)]:
                    label=f'{name}_{lookback}_{top_n}'
                    print(f'Grid {name}: lookback={lookback}, top_n={top_n}',flush=True)
                    if lookback==220 and top_n==6 and scores is not None:
                        old_scores = pd.read_parquet(OUT/'author_snapshot_replay/industry_selection.parquet' if name=='RRG' else source/'industry_momentum_selection.parquet')
                        # Parquet readers preserve different datetime precision;
                        # compare identical timestamps at a common precision.
                        old_scores.index = old_scores.index.as_unit('ns')
                        scores.index = scores.index.as_unit('ns')
                        pd.testing.assert_frame_equal(scores.loc[BACKTEST_START:],old_scores.loc[BACKTEST_START:])
                    if (name,lookback,top_n) in cached:
                        weights = pd.read_parquet(target/f'{label}_weights.parquet')
                    else:
                        # strict=False matches primary RRG reproduction; failed rows must be disclosed.
                        weights = mask_listed(batch_optimize(scores,exposures=exposures,dates=dates,preset='base',strict=False),meta)
                    failed = weights.loc[BACKTEST_START:].isna().all(axis=1)
                    if failed.any():
                        raise ValueError(f'Optimizer failed: {name}/{lookback}/{top_n}: {list(failed[failed].index)}')
                    extra = set(weights.columns)-set(cols)
                    assert not extra
                    weights = weights.reindex(index=dates,columns=cols).fillna(0.)
                    pf = run_vbt_backtest(weights.loc[BACKTEST_START:],close.loc[BACKTEST_START:],open_.loc[BACKTEST_START:],
                        fees=.0003,slippage=0.,init_cash=1e7)
                    checks[label]=validate(pf,weights,close.loc[BACKTEST_START:],run)
                    if (name,lookback,top_n) in cached:
                        prior_nav=pd.read_csv(target/f'{label}_nav.csv',index_col=0).iloc[:,0]
                        np.testing.assert_allclose(pf.value().to_numpy(),prior_nav.to_numpy(),rtol=1e-12)
                    if lookback==220 and top_n==6:
                        original_name='RRG' if name=='RRG' else 'Industry_momentum_220_6'
                        old_nav=pd.read_csv(source/f'{original_name}_sell_first_nav.csv',index_col=0).iloc[:,0]
                        np.testing.assert_allclose(pf.value().to_numpy(),old_nav.to_numpy(),rtol=1e-12)
                    weights.to_parquet(target/f'{label}_weights.parquet')
                    pf.value().to_csv(target/f'{label}_nav.csv')
                    row=portfolio_stats(pf).to_dict()
                    row.update(strategy=name,lookback=lookback,top_n=top_n,
                        profit_cny=checks[label]['profit_cny'],fees_cny=checks[label]['fees_cny'])
                    rows.append(row)
                    pd.DataFrame(rows).to_csv(target/'parameter_grid.csv',index=False)
    finally:
        vbt.settings.portfolio['call_seq']=original_seq
    write_json(target/'validation.json',{'grid_accounting':checks,'base_settings_replay_exactly':True,
        'actual_daily_holdings_reconcile':True,'selected_industry_target_caps_checked':True,
        'upstream_commit':verify_upstream(),'author_sha256':run['input_database_sha256']})
    write_json(target/'run.json',dict(run,lookback_grid=[180,220,260],top_n_grid=[4,6,8],
        rrg_grid_definition='Change diffusion lookback and RS-ratio lookback together; RS-momentum=60, smoothing=20 fixed.',
        warmup_260='Signal start 2021-03-05 matches actual 2020-01-07 membership waterline; formal window and first executed formation unchanged.',
        data_scope='Author snapshot versus separately downloaded Tushare-compatible snapshot; not independent-vendor certification.',
        grid_purpose='Prespecified historical sensitivity, all settings reported; not tuning or out-of-sample selection.'))
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4),sharey=True)
    table=pd.DataFrame(rows)
    for ax,name in zip(axes,['RRG','Industry_momentum']):
        values=table.loc[table.strategy.eq(name)].pivot(index='lookback',columns='top_n',values='年化收益率')*100
        ax.imshow(values,aspect='auto',cmap='RdYlGn',vmin=table['年化收益率'].min()*100,vmax=table['年化收益率'].max()*100)
        ax.set(title=name,xlabel='Top N industries',xticks=range(3),xticklabels=values.columns,
               yticks=range(3),yticklabels=values.index)
        for i in range(3):
            for j in range(3):
                ax.text(j,i,f'{values.iloc[i,j]:.2f}%',ha='center',va='center')
    axes[0].set_ylabel('Lookback trading days')
    fig.suptitle('Fixed execution and 3bp/side | Net CAGR sensitivity')
    fig.tight_layout()
    fig.savefig(target/'parameter_grid.png',dpi=160)
    print(table[['strategy','lookback','top_n','年化收益率','最大回撤']].to_string(index=False),flush=True)


if __name__=='__main__':
    main()
