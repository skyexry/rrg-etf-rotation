"""Audit fee drag and original-engine cash sequencing without changing signals."""
import json
import numpy as np
import pandas as pd

from rrg_pipeline import OUT, SIGNAL_START, BACKTEST_START, END, bind_upstream, write_json
from run_rrg_baselines import validate


def main():
    target = OUT/'author_baseline_comparison'
    run = json.loads((target/'run.json').read_text())
    bind_upstream(run['input_database'])
    from src.data_provider import fetch_etf_daily
    from src.backtest import run_vbt_backtest, portfolio_stats
    import vectorbt as vbt
    names = ['RRG', 'Industry_momentum_220_6', 'ETF_equal_weight']
    weights = {n: pd.read_parquet(target/f'{n}_weights.parquet') for n in names}
    cols = list(weights['RRG'].columns)
    close, open_, _ = fetch_etf_daily(cols, SIGNAL_START, END, lookback=25)
    close, open_ = close[cols].loc[BACKTEST_START:], open_[cols].loc[BACKTEST_START:]
    default_sequence = vbt.settings.portfolio['call_seq']
    rows = []
    sell_first_navs, accounting = {}, {}
    for mode, fee, seq in [('original_net', .0003, default_sequence),
                           ('original_zero_fees', 0., default_sequence),
                           ('sell_first_net', .0003, 'auto')]:
        vbt.settings.portfolio['call_seq'] = seq
        try:
            for name, w in weights.items():
                pf = run_vbt_backtest(w.loc[BACKTEST_START:], close, open_, fees=fee,
                                      slippage=0., init_cash=1e7)
                nav, cash = pf.value(), pf.cash()
                orders = pf.orders.records_readable
                np.testing.assert_allclose(orders.Fees, orders.Size*orders.Price*fee, atol=1e-6)
                assert cash.min() >= -1e-5
                if mode == 'original_net':
                    prior = pd.read_csv(target/f'{name}_nav.csv', index_col=0).iloc[:, 0]
                    np.testing.assert_allclose(nav.to_numpy(), prior.to_numpy(), rtol=1e-12)
                row = portfolio_stats(pf).to_dict()
                row.update({'strategy': name, 'mode': mode,
                    'mean_cash_fraction': float((cash/nav).mean()),
                    'median_posttrade_cash_fraction': float((cash/nav).loc[orders.Timestamp.unique()].median()),
                    'total_fees_cny': float(orders.Fees.sum())})
                rows.append(row)
                if mode == 'sell_first_net':
                    accounting[name] = validate(pf, w, close, run)
                    sell_first_navs[name] = nav/run['initial_cash']
                    nav.to_csv(target/f'{name}_sell_first_nav.csv')
                    orders.to_csv(target/f'{name}_sell_first_orders.csv', index=False)
        finally:
            vbt.settings.portfolio['call_seq'] = default_sequence
    result = pd.DataFrame(rows).set_index(['mode', 'strategy'])
    # Complete the 2x2: target-NaN handling and execution sequence are distinct.
    raw_rrg = pd.read_parquet(OUT/'author_snapshot_replay/rrg_weights.parquet')
    original_close, original_open, _ = fetch_etf_daily(list(raw_rrg.columns), SIGNAL_START, END, lookback=25)
    factorial = []
    for label, seq in [('default', default_sequence), ('sell_first', 'auto')]:
        vbt.settings.portfolio['call_seq'] = seq
        try:
            pf = run_vbt_backtest(raw_rrg.loc[BACKTEST_START:],
                original_close[raw_rrg.columns].loc[BACKTEST_START:],
                original_open[raw_rrg.columns].loc[BACKTEST_START:], fees=.0003, slippage=0., init_cash=1e7)
            if label == 'default':
                old = pd.read_csv(OUT/'author_snapshot_replay/RRG_base_nav.csv', index_col=0).iloc[:, 0]
                np.testing.assert_allclose(pf.value().to_numpy(), old.to_numpy(), rtol=1e-12)
            o = pf.orders.records_readable
            np.testing.assert_allclose(o.Fees, o.Size*o.Price*.0003, atol=1e-6)
            assert pf.cash().min() >= -1e-5
            factorial.append(dict(portfolio_stats(pf), exit_rule='retain_NaN', sequence=label,
                                  mean_cash_fraction=float((pf.cash()/pf.value()).mean())))
            pf.value().to_csv(target/f'RRG_retain_NaN_{label}_nav.csv')
        finally:
            vbt.settings.portfolio['call_seq'] = default_sequence
    for mode, label in [('original_net', 'default'), ('sell_first_net', 'sell_first')]:
        factorial.append(dict(result.loc[(mode, 'RRG')], exit_rule='explicit_zero', sequence=label))
    pd.DataFrame(factorial).set_index(['exit_rule', 'sequence']).to_csv(target/'RRG_2x2_attribution.csv')
    result.to_csv(target/'execution_diagnostics.csv')
    result.loc['sell_first_net'].T.to_csv(target/'sell_first_performance.csv')
    pd.DataFrame(sell_first_navs).to_csv(target/'sell_first_net_nav.csv')
    write_json(target/'sell_first_validation.json', accounting)
    write_json(target/'sell_first_run.json', dict(run, call_seq='auto',
        execution_note='Original upstream engine with vectorbt auto sequence: sell before buy. Signals and target weights unchanged.'))
    write_json(target/'execution_audit.json', {
        'original_nav_replayed_exactly': True, 'fee_and_cash_checks_passed': True,
        'changed_signals_or_targets': False,
        'auto_sequence': 'Same upstream backtest function; vectorbt call_seq=auto sorts sell orders before buys.',
        'original_call_seq': str(default_sequence),
        'scope': 'Execution sensitivity; parameters 220 days and top 6 held fixed.'})
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True,
                             gridspec_kw={'height_ratios': [2, 1]})
    for name, nav in sell_first_navs.items():
        annual = result.loc[('sell_first_net', name), '年化收益率']
        axes[0].plot(nav.index, nav, label=f'{name}: CAGR {annual:.2%}')
        axes[1].plot(nav.index, nav/nav.cummax()-1)
    axes[0].set(title='Same signals | Sell before buy | Net of 3bp per side', ylabel='NAV (initial = 1)')
    axes[0].legend()
    axes[1].set(ylabel='Drawdown')
    for ax in axes:
        ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(target/'sell_first_comparison.png', dpi=160)
    print(result.to_string())


if __name__ == '__main__':
    main()
