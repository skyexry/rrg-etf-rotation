"""Reconcile RRG execution differences to monthly and ETF-level P&L."""
import hashlib
import json

import numpy as np
import pandas as pd

from rrg_pipeline import OUT, SIGNAL_START, BACKTEST_START, END, bind_upstream, write_json, verify_upstream


def ledger(pf, target_weights, close, open_, initial_cash):
    nav = pf.value()
    orders = pf.orders.records_readable
    columns, dates = close.columns, close.index
    orders['signed_size'] = orders.Size.where(orders.Side.eq('Buy'), -orders.Size)
    orders['signed_notional'] = orders.signed_size * orders.Price

    def pivot(field):
        return orders.pivot_table(index='Timestamp', columns='Column', values=field,
            aggfunc='sum').reindex(index=dates, columns=columns).fillna(0.)

    dq, notional, fees = pivot('signed_size'), pivot('signed_notional'), pivot('Fees')
    q = dq.cumsum()
    prior_q = q.shift(1).fillna(0.)
    mark = close.ffill()
    prior_mark = mark.shift(1)
    # Exact cash-and-shares identity, including trades at adjusted opening prices.
    holding_pnl = (prior_q * (mark-prior_mark)).fillna(0.)
    trading_pnl = (dq*mark).fillna(0.) - notional
    pnl = holding_pnl + trading_pnl - fees
    cash = initial_cash - (notional.sum(axis=1)+fees.sum(axis=1)).cumsum()
    reconstructed = cash + (q*mark).fillna(0.).sum(axis=1)
    expected_pnl = nav.diff().fillna(nav.iloc[0]-initial_cash)
    nav_error = float((reconstructed-nav).abs().max())
    pnl_error = float((pnl.sum(axis=1)-expected_pnl).abs().max())
    np.testing.assert_allclose(reconstructed, nav, atol=1e-5, rtol=1e-10)
    np.testing.assert_allclose(pnl.sum(axis=1), expected_pnl, atol=1e-5, rtol=1e-8)
    np.testing.assert_allclose(fees.sum().sum(), orders.Fees.sum(), atol=1e-6)

    # Distance measured immediately after execution at that day's opening marks.
    # Absent opening quote: last observed close; holdings and missing marks audited.
    opening_mark = open_.where(open_.gt(0)).combine_first(prior_mark)
    assert not ((q.abs()>1e-7) & opening_mark.isna()).any().any()
    opening_assets = (q*opening_mark).fillna(0.)
    opening_nav = cash+opening_assets.sum(axis=1)
    actual_open_weights = opening_assets.div(opening_nav, axis=0)
    instructions = target_weights.reindex(dates).shift(1)
    formation_days = pd.Series(True, index=target_weights.index).reindex(dates).shift(1).fillna(False).astype(bool)
    rebalance_days = dates[formation_days]
    desired = instructions.loc[rebalance_days].fillna(0.)
    actual = actual_open_weights.loc[rebalance_days]
    desired_cash = 1-desired.sum(axis=1)
    actual_cash = cash.loc[rebalance_days]/opening_nav.loc[rebalance_days]
    gap = ((actual-desired).abs().sum(axis=1)+(actual_cash-desired_cash).abs())/2
    # NaN has no instruction in the original strategy. Distance to zero there
    # measures legacy holdings outside the displayed allocation, not a violation.
    legacy = actual.where(instructions.loc[rebalance_days].isna(), 0.).sum(axis=1)
    rebalance = pd.DataFrame({'target_distance_half_L1': gap,
        'cash_fraction_after_trade': actual_cash, 'legacy_weight_without_instruction': legacy,
        'actual_etf_count': (actual>1e-7).sum(axis=1), 'target_etf_count': (desired>1e-7).sum(axis=1)})
    monthly = pd.DataFrame({'end_nav_cny': nav.resample('ME').last(),
        'pnl_cny': expected_pnl.resample('ME').sum(),
        'fees_cny': fees.sum(axis=1).resample('ME').sum(),
        'mean_cash_fraction': (cash/nav).resample('ME').mean(),
        'end_cash_fraction': (cash/nav).resample('ME').last(),
        'mean_etf_count': (q>1e-7).sum(axis=1).resample('ME').mean()})
    monthly['start_nav_cny'] = monthly.end_nav_cny.shift(1).fillna(initial_cash)
    monthly['return'] = monthly.pnl_cny/monthly.start_nav_cny
    monthly['mean_target_distance'] = rebalance.target_distance_half_L1.resample('ME').mean()
    monthly_etf = pnl.resample('ME').sum()
    np.testing.assert_allclose(monthly_etf.sum(axis=1), monthly.pnl_cny, atol=1e-5)
    summary = {'annualized_return': float(pf.annualized_return()), 'ending_nav_cny': float(nav.iloc[-1]),
        'profit_cny': float(nav.iloc[-1]-initial_cash), 'total_fees_cny': float(fees.sum().sum()),
        'mean_cash_fraction': float((cash/nav).mean()),
        'mean_target_distance': float(gap.mean()),
        'mean_legacy_weight_after_trade': float(legacy.mean()),
        'max_nav_reconstruction_error_cny': nav_error, 'max_daily_pnl_error_cny': pnl_error}
    holdings = q.where(q.abs()>1e-7).stack().rename('adjusted_share_units').to_frame()
    holdings['close_value_cny'] = (q*mark).stack().reindex(holdings.index)
    holdings['close_weight'] = holdings.close_value_cny / holdings.index.get_level_values(0).map(nav)
    return monthly, monthly_etf, rebalance, holdings, summary


def main():
    source = OUT/'author_baseline_comparison'
    target = source/'execution_attribution'
    target.mkdir(parents=True, exist_ok=True)
    run = json.loads((source/'run.json').read_text())
    with open(run['input_database'], 'rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == run['input_database_sha256']
    assert verify_upstream() == run['upstream_commit']
    bind_upstream(run['input_database'])
    from src.data_provider import fetch_etf_daily, fetch_etf_meta
    from src.backtest import run_vbt_backtest
    import vectorbt as vbt
    raw = pd.read_parquet(OUT/'author_snapshot_replay/rrg_weights.parquet')
    zero = pd.read_parquet(source/'RRG_weights.parquet')
    meta = fetch_etf_meta(only_listed=True, drop_qdii=True)
    close, open_, _ = fetch_etf_daily(list(zero.columns), SIGNAL_START, END, lookback=25)
    close, open_ = close.loc[BACKTEST_START:], open_.loc[BACKTEST_START:]
    old_seq, old_log = vbt.settings.portfolio['call_seq'], vbt.settings.portfolio['log']
    summaries, monthly_all, etf_all = {}, {}, {}
    for policy, weights in [('retain', raw), ('exit', zero)]:
        for sequence in ['default', 'auto']:
            name = policy+'_'+sequence
            print('Attributing '+name, flush=True)
            vbt.settings.portfolio['call_seq'] = sequence
            vbt.settings.portfolio['log'] = True
            try:
                pf = run_vbt_backtest(weights.loc[BACKTEST_START:], close[weights.columns],
                    open_[weights.columns], fees=.0003, slippage=0., init_cash=1e7)
            finally:
                vbt.settings.portfolio['call_seq'], vbt.settings.portfolio['log'] = old_seq, old_log
            if policy == 'retain':
                old_path = source/f'RRG_retain_NaN_{"sell_first" if sequence=="auto" else "default"}_nav.csv'
            else:
                old_path = source/('RRG_sell_first_nav.csv' if sequence=='auto' else 'RRG_nav.csv')
            np.testing.assert_allclose(pf.value().to_numpy(), pd.read_csv(old_path,index_col=0).iloc[:,0], rtol=1e-12)
            m, e, r, holdings, summary = ledger(pf, weights, close[weights.columns], open_[weights.columns], 1e7)
            logs = pf.logs.records_readable
            logs.to_parquet(target/f'{name}_order_logs.parquet', index=False)
            status = logs.groupby(['Result Status', 'Result Status Info'], dropna=False).size().rename('count')
            status.to_csv(target/f'{name}_order_status.csv')
            m.to_csv(target/f'{name}_monthly.csv')
            e.to_parquet(target/f'{name}_monthly_etf_pnl.parquet')
            r.to_csv(target/f'{name}_rebalance.csv')
            holdings.to_parquet(target/f'{name}_daily_holdings.parquet')
            monthly_all[name], etf_all[name], summaries[name] = m, e, summary
    comparisons = [('execution_only', 'exit_default', 'exit_auto'),
                   ('exit_rule_only', 'retain_default', 'exit_default'),
                   ('original_to_adjusted', 'retain_default', 'exit_auto')]
    for label, a, b in comparisons:
        ma, mb = monthly_all[a], monthly_all[b]
        diff = pd.DataFrame({'a_return': ma['return'], 'b_return': mb['return'],
            'a_minus_b_pnl_cny': ma.pnl_cny-mb.pnl_cny,
            'a_minus_b_fee_saving_cny': mb.fees_cny-ma.fees_cny,
            'a_cash_fraction': ma.mean_cash_fraction, 'b_cash_fraction': mb.mean_cash_fraction,
            'a_target_distance': ma.mean_target_distance, 'b_target_distance': mb.mean_target_distance})
        diff['a_minus_b_price_pnl_cny'] = diff.a_minus_b_pnl_cny-diff.a_minus_b_fee_saving_cny
        de = etf_all[a].subtract(etf_all[b], fill_value=0.)
        np.testing.assert_allclose(de.sum(axis=1), diff.a_minus_b_pnl_cny, atol=1e-5)
        np.testing.assert_allclose(diff.a_minus_b_pnl_cny.sum(),
            summaries[a]['ending_nav_cny']-summaries[b]['ending_nav_cny'], atol=1e-5)
        diff.to_csv(target/f'{label}_monthly.csv')
        de.to_parquet(target/f'{label}_monthly_etf_pnl_difference.parquet')
        total = de.sum().rename('a_minus_b_net_pnl_cny').to_frame().sort_values('a_minus_b_net_pnl_cny', ascending=False)
        total['tracked_index'] = meta.index_name.reindex(total.index)
        total.to_csv(target/f'{label}_etf_contributors.csv')
        summaries[label] = {'a': a, 'b': b, 'net_profit_difference_cny': float(diff.a_minus_b_pnl_cny.sum()),
            'price_pnl_difference_cny': float(diff.a_minus_b_price_pnl_cny.sum()),
            'fee_saving_cny': float(diff.a_minus_b_fee_saving_cny.sum()),
            'positive_months': int(diff.a_minus_b_pnl_cny.gt(1e-5).sum()),
            'negative_months': int(diff.a_minus_b_pnl_cny.lt(-1e-5).sum()),
            'top_positive_months': {str(d.date()): float(v) for d,v in diff.a_minus_b_pnl_cny.nlargest(5).items()},
            'top_negative_months': {str(d.date()): float(v) for d,v in diff.a_minus_b_pnl_cny.nsmallest(5).items()}}
    write_json(target/'summary.json', summaries)
    write_json(target/'validation.json', {'all_four_navs_match_saved_replays': True,
        'all_daily_cash_shares_and_pnl_identities_passed': True,
        'all_monthly_etf_contributions_add_up': True,
        'all_total_profit_differences_reconcile': True,
        'input_sha256': run['input_database_sha256'], 'upstream_commit': verify_upstream(),
        'cash_interest': 0, 'currency': 'CNY',
        'target_distance': 'half L1 across ETF weights and cash; opening marks after trades. Original NaN cells treated as zero only for diagnostic desired allocation.',
        'pnl_formula': 'prior shares*(close-prev close) + traded shares*close - signed traded notional - fees',
        'interpretation': 'Actual holding/accounting attribution; cash and allocation effects are joint, not separately identified causal effects. Different capital paths affect absolute P&L.'})
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    a, b = monthly_all['exit_default'], monthly_all['exit_auto']
    axes[0].plot(a.index, a.mean_cash_fraction*100, label='Default order sequence')
    axes[0].plot(b.index, b.mean_cash_fraction*100, label='Sell before buy')
    axes[0].set(ylabel='Mean cash (%)', title='Same RRG targets: execution-only attribution')
    axes[0].legend()
    axes[1].plot(a.index, a.mean_target_distance*100)
    axes[1].plot(b.index, b.mean_target_distance*100)
    axes[1].set(ylabel='Target distance (%)')
    delta = (a.pnl_cny-b.pnl_cny)/10000
    axes[2].bar(delta.index, delta, width=20, color=np.where(delta.ge(0),'#20805e','#cf6244'))
    axes[2].set(ylabel='Default minus sell-first\nprofit (CNY 10,000)')
    for ax in axes:
        ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(target/'monthly_attribution.png', dpi=160)
    print(json.dumps(summaries, ensure_ascii=False, indent=2, default=str), flush=True)


if __name__ == '__main__':
    main()
