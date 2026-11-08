"""Fixed author-snapshot comparison; all portfolio calculations use upstream code."""
import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from rrg_pipeline import (OUT, SIGNAL_START, BACKTEST_START, END, EXCLUDE,
                          bind_upstream, verify_upstream, write_json)


def industry_momentum(close, dates, lookback=220, top_n=6):
    # Use the original rank/top-N selector on returns instead of diffusion.
    from src.strategy import select_by_diffusion
    returns = close.drop(columns=EXCLUDE, errors='ignore').sort_index(axis=1).pct_change(
        periods=lookback, fill_method=None)
    return returns, select_by_diffusion(returns, top_n=top_n).loc[dates].astype(float)


def validate(pf, weights, close, run):
    nav = pf.value()
    orders = pf.orders.records_readable
    assert nav.index.is_unique and nav.index.is_monotonic_increasing
    assert np.isfinite(nav).all() and nav.gt(0).all()
    assert np.isfinite(weights.to_numpy()).all() and weights.min().min() >= -1e-8
    assert weights.sum(axis=1).max() <= 1 + 1e-5
    formation_offsets = nav.index.get_indexer(weights.index.intersection(nav.index)) + 1
    execution_days = nav.index[formation_offsets[formation_offsets < len(nav)]]
    assert orders.Timestamp.isin(execution_days).all()
    np.testing.assert_allclose(orders.Fees, orders.Size * orders.Price * run['fees'], atol=1e-6)
    signed = orders.Size.where(orders.Side.eq('Buy'), -orders.Size)
    assert signed.groupby(orders.Column).cumsum().min() >= -1e-6
    cash = run['initial_cash'] - (signed * orders.Price).sum() - orders.Fees.sum()
    positions = signed.groupby(orders.Column).sum()
    last = close.ffill().iloc[-1].reindex(positions.index)
    assert last.notna().all() and cash >= -1e-5
    np.testing.assert_allclose(cash + (positions * last).sum(), nav.iloc[-1], atol=1e-5)
    total = nav.iloc[-1] / run['initial_cash'] - 1
    np.testing.assert_allclose(pf.total_return(), total, atol=1e-10)
    np.testing.assert_allclose(pf.annualized_return(), (1+total)**(252/len(nav))-1, atol=1e-10)
    # Turnover = executed buy+sell value / preceding daily portfolio NAV, annualized.
    prior_nav = nav.shift(1).fillna(run['initial_cash'])
    notional = orders.Size * orders.Price
    turnover = (notional / orders.Timestamp.map(prior_nav)).sum() * 252 / len(nav)
    return {'passed': True, 'orders_next_session_after_formation': True,
            'trading_days': len(nav), 'first_order': str(orders.Timestamp.min().date()),
            'ending_value_cny': float(nav.iloc[-1]), 'profit_cny': float(nav.iloc[-1]-run['initial_cash']),
            'fees_cny': float(orders.Fees.sum()), 'annualized_two_way_turnover': float(turnover),
            'orders': len(orders)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    replay = OUT/'author_snapshot_replay'
    original_run = json.loads((replay/'run.json').read_text())
    with database.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    assert digest == original_run['input_database_sha256']
    assert verify_upstream() == original_run['upstream_commit']
    bind_upstream(database)
    from src.data_provider import fetch_etf_meta, fetch_etf_daily, fetch_rrg_dataset
    from src.exposure import ETFExposureBuilder
    from src.store import RRGStore
    from src.optimizer import batch_optimize
    from src.backtest import (build_exposure_panel, mask_listed, run_vbt_backtest, portfolio_stats)

    target = OUT/'author_baseline_comparison'
    target.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(replay/'candidate_etf_panel.parquet')
    rrg = pd.read_parquet(replay/'rrg_weights.parquet')
    meta = fetch_etf_meta(only_listed=True, drop_qdii=True)
    codes = sorted(meta.index)
    close, open_, _ = fetch_etf_daily(codes, SIGNAL_START, END, lookback=25)
    print('Loading original ETF exposure mapping', flush=True)
    with RRGStore(database) as store:
        with ETFExposureBuilder(codes, SIGNAL_START, END, store=store, classification='zx') as builder:
            iw = builder.build_industry_weights()
    exposures = build_exposure_panel(panel, iw)
    dates = rrg.index
    assert set(dates).issubset(exposures)
    _, industry_close, _ = fetch_rrg_dataset(start_date=SIGNAL_START, end_date=END, classification='zx')
    # Snapshot industry data end Aug 28 while ETF calendar ends Aug 31.
    # Carry last observed index close on that terminal date (its order would
    # occur after this backtest). No backward fill or future values.
    missing_industry_days = close.index.difference(industry_close.index)
    industry_close = industry_close.reindex(close.index).ffill()
    returns, scores = industry_momentum(industry_close, dates)
    assert scores.loc[BACKTEST_START:].sum(axis=1).eq(6).all()
    cut = pd.Timestamp('2024-06-28')
    past_dates = dates[dates <= cut]
    altered = industry_close.copy()
    altered.loc[altered.index > cut] *= np.linspace(.2, 10, len(altered.columns))
    _, shocked = industry_momentum(altered, past_dates)
    _, prefix = industry_momentum(industry_close.loc[:cut], past_dates)
    pd.testing.assert_frame_equal(scores.loc[past_dates], shocked)
    pd.testing.assert_frame_equal(scores.loc[past_dates], prefix)
    print('Optimizing fixed 220-day/top-6 industry momentum', flush=True)
    momentum = mask_listed(batch_optimize(scores, exposures=exposures, dates=dates,
                                         preset='base', strict=True), meta)
    # Same investable exposure rows as the two optimized strategies.
    equal = pd.DataFrame({d: pd.Series(1/len(exposures[d]), index=exposures[d].index)
                          for d in dates}).T
    raw = {'RRG': rrg, 'Industry_momentum_220_6': momentum, 'ETF_equal_weight': equal}
    columns = sorted(set().union(*(set(w.columns) for w in raw.values())))
    assert set(columns).issubset(close.columns)
    close, open_ = close[columns].loc[BACKTEST_START:], open_[columns].loc[BACKTEST_START:]
    run = {'input_database': str(database), 'input_database_sha256': digest,
           'upstream_commit': verify_upstream(), 'backtest_start': BACKTEST_START, 'end': END,
           'fees': .0003, 'slippage': 0., 'initial_cash': 1e7,
           'momentum_lookback': 220, 'momentum_top_n': 6,
           'industry_calendar_carry_dates': [str(d.date()) for d in missing_industry_days if d >= pd.Timestamp(SIGNAL_START)],
           'execution': 'Original run_vbt_backtest: month-end signal, next-session adjusted open; 252 days/year.',
           'target_policy': 'All three strategies: absent ETF = zero target on formation dates; no instruction on other dates.',
           'pool': 'Same PIT exposure-mapped representatives from original candidate panel; equal weight across ETFs, not industries.',
           'columns': 'Identical sorted ETF column order for all three strategies.',
           'original_reference': 'Preserved separately; original RRG NaNs retain prior shares.',
           'limitations': ['Author snapshot current-listed ETF universe has survivorship bias.',
                          'Quarterly holdings often contain only top ten stocks; float MV proxy.',
                          'Upstream cash-sharing order execution and fractional adjusted units retained.',
                          'Fees cover 3bp per side; additional slippage/market impact are zero.',
                          'The full period is a historical comparison, not an untouched out-of-sample test.']}
    scores.to_parquet(target/'industry_momentum_selection.parquet')
    returns.loc[dates].to_parquet(target/'industry_220d_returns.parquet')
    pd.Series({d: len(exposures[d]) for d in dates}, name='etf_count').to_csv(target/'pool_size.csv')
    metrics, validations, navs = {}, {'momentum_future_shock_and_prefix_checks_passed': True}, {}
    for name, w in raw.items():
        print('Backtesting '+name, flush=True)
        w.to_parquet(target/f'{name}_raw_weights.parquet')
        w = w.reindex(index=dates, columns=columns).fillna(0.)
        for code in w.columns:
            assert (w.index[w[code] > 1e-8] >= meta.loc[code, 'list_date']).all()
        if name == 'ETF_equal_weight':
            for _, row in w.iterrows():
                positive = row[row > 0]
                np.testing.assert_allclose(positive, 1/len(positive), atol=1e-12)
        w.to_parquet(target/f'{name}_weights.parquet')
        pf = run_vbt_backtest(w.loc[BACKTEST_START:], close, open_, fees=run['fees'],
                              slippage=run['slippage'], init_cash=run['initial_cash'])
        checks = validate(pf, w, close, run)
        metrics[name] = portfolio_stats(pf)
        for key in ['profit_cny', 'fees_cny', 'annualized_two_way_turnover']:
            metrics[name][key] = checks[key]
        validations[name] = checks
        navs[name] = pf.value()/run['initial_cash']
        pf.value().to_csv(target/f'{name}_nav.csv')
        pf.orders.records_readable.to_csv(target/f'{name}_orders.csv', index=False)

    # Verify the unchanged original execution still reproduces the prior 12.58%.
    old_cols = rrg.columns.intersection(close.columns)
    old_close, old_open = close[old_cols], open_[old_cols]
    original_pf = run_vbt_backtest(rrg[old_cols].loc[BACKTEST_START:], old_close[old_cols].loc[BACKTEST_START:],
                                  old_open[old_cols].loc[BACKTEST_START:], fees=.0003, slippage=0., init_cash=1e7)
    prior = pd.read_csv(replay/'RRG_base_nav.csv', index_col=0).iloc[:, 0]
    np.testing.assert_allclose(original_pf.value().to_numpy(), prior.to_numpy(), rtol=1e-12)
    validations['original_reference_nav_matches_previous_replay'] = True
    pd.DataFrame(metrics).to_csv(target/'performance.csv')
    pd.DataFrame(navs).to_csv(target/'net_nav.csv')
    write_json(target/'run.json', run)
    write_json(target/'validation.json', validations)
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    for name, nav in navs.items():
        axes[0].plot(nav.index, nav, label=f'{name}: CAGR {metrics[name]["年化收益率"]:.2%}')
        axes[1].plot(nav.index, nav/nav.cummax()-1)
    axes[0].set(title='Author snapshot | Common portfolio exit policy | Net of 3bp/side', ylabel='NAV (initial = 1)')
    axes[0].legend()
    axes[1].set(ylabel='Drawdown')
    for ax in axes:
        ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(target/'comparison.png', dpi=160)
    print(pd.DataFrame(metrics).to_string(), flush=True)
    print(json.dumps(validations, indent=2), flush=True)


if __name__ == '__main__':
    main()
