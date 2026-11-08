"""Fixed-parameter historical stability and transaction-friction checks."""
import hashlib
import json
import argparse

import numpy as np
import pandas as pd

from rrg_pipeline import OUT, SIGNAL_START, BACKTEST_START, END, bind_upstream, verify_upstream, write_json
from run_rrg_baselines import validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ablation-candidates', action='store_true',
                        help='Check the pure Q1 and no-industry-cap ablation candidates')
    args = parser.parse_args()
    source = OUT/'author_baseline_comparison'
    if args.ablation_candidates:
        source = source/'ablation'
    target = source/'time_cost_checks'
    target.mkdir(parents=True, exist_ok=True)
    run = json.loads((source/('run.json' if args.ablation_candidates else 'sell_first_run.json')).read_text())
    with open(run['input_database'], 'rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == run['input_database_sha256']
    assert verify_upstream() == run['upstream_commit']
    bind_upstream(run['input_database'])
    from src.data_provider import fetch_etf_daily
    from src.backtest import run_vbt_backtest, portfolio_stats
    import vectorbt as vbt

    names = (['RRG_Q1_only', 'Full_report_no_industry_cap'] if args.ablation_candidates else
             ['RRG', 'Industry_momentum_220_6', 'ETF_equal_weight'])
    weights = {n: pd.read_parquet(source/f'{n}_weights.parquet') for n in names}
    cols = list(weights[names[0]].columns)
    assert all(list(w.columns)==cols for w in weights.values())
    close, open_, _ = fetch_etf_daily(cols, SIGNAL_START, END, lookback=25)
    close, open_ = close[cols].loc[BACKTEST_START:], open_[cols].loc[BACKTEST_START:]
    rows, checks, base_navs = [], {}, {}
    old_seq = vbt.settings.portfolio['call_seq']
    vbt.settings.portfolio['call_seq'] = 'auto'
    try:
        for bp in [3, 5, 10, 20]:
            for name, w in weights.items():
                print(f'Running {name}: {bp} bp/side', flush=True)
                pf = run_vbt_backtest(w.loc[BACKTEST_START:], close, open_, fees=bp/10000,
                                      slippage=0., init_cash=run['initial_cash'])
                case_run = dict(run, fees=bp/10000)
                accounting = validate(pf, w, close, case_run)
                checks[f'{name}_{bp}bp'] = accounting
                stats = portfolio_stats(pf).to_dict()
                stats.update({'strategy': name, 'one_way_total_friction_bp': bp,
                    'profit_cny': accounting['profit_cny'], 'fees_cny': accounting['fees_cny'],
                    'annualized_two_way_turnover': accounting['annualized_two_way_turnover']})
                rows.append(stats)
                nav = pf.value()
                nav.to_csv(target/f'{name}_{bp}bp_nav.csv')
                pf.orders.records_readable.to_csv(target/f'{name}_{bp}bp_orders.csv', index=False)
                if bp == 3:
                    previous_file = source/f'{name}_nav.csv' if args.ablation_candidates else source/f'{name}_sell_first_nav.csv'
                    prior = pd.read_csv(previous_file, index_col=0).iloc[:, 0]
                    np.testing.assert_allclose(nav.to_numpy(), prior.to_numpy(), rtol=1e-12)
                    base_navs[name] = nav
    finally:
        vbt.settings.portfolio['call_seq'] = old_seq
    cost = pd.DataFrame(rows)
    cost['annual_return_rank'] = cost.groupby('one_way_total_friction_bp')['年化收益率'].rank(ascending=False, method='min')
    cost.to_csv(target/'cost_sensitivity.csv', index=False)
    cost.pivot(index='one_way_total_friction_bp', columns='strategy', values='年化收益率').to_csv(target/'cost_cagr.csv')

    navs = pd.DataFrame(base_navs)
    month_end = navs.resample('ME').last()
    monthly = month_end.pct_change(fill_method=None)
    monthly.iloc[0] = month_end.iloc[0]/run['initial_cash']-1
    # Calendar-month horizons, not a 252-session approximation.
    rolling = (1+monthly).rolling(12, min_periods=12).apply(np.prod, raw=True)-1
    rolling = rolling.dropna(how='any')
    ranks = rolling.rank(axis=1, ascending=False, method='min')
    annual = (1+monthly).groupby(monthly.index.year).prod()-1
    annual.index.name = 'year'
    year_ranks = annual.rank(axis=1, ascending=False, method='min')
    # Calendar-year returns remain period returns for the two partial years.
    for year in annual.index:
        subset = navs.loc[navs.index.year == year]
        prev = navs.loc[navs.index < subset.index[0]].iloc[-1] if year != annual.index[0] else run['initial_cash']
        np.testing.assert_allclose(annual.loc[year], subset.iloc[-1]/prev-1, atol=1e-10)
    for date, row in rolling.iterrows():
        end_pos = month_end.index.get_loc(date)
        before = month_end.iloc[end_pos-12] if end_pos >= 12 else run['initial_cash']
        np.testing.assert_allclose(row, month_end.loc[date]/before-1, atol=1e-10)
    monthly.to_csv(target/'monthly_returns_3bp.csv')
    annual.to_csv(target/'annual_returns_3bp.csv')
    year_ranks.to_csv(target/'annual_ranks_3bp.csv')
    rolling.to_csv(target/'rolling_12_calendar_month_returns_3bp.csv')
    ranks.to_csv(target/'rolling_12_month_ranks_3bp.csv')
    stability = pd.DataFrame({'windows': len(rolling), 'top_rank_windows': ranks.eq(1).sum(),
        'positive_return_windows': rolling.gt(0).sum(), 'median_12m_return': rolling.median(),
        'worst_12m_return': rolling.min(), 'best_12m_return': rolling.max(),
        'mean_rank': ranks.mean()})
    stability.to_csv(target/'rolling_summary.csv')
    pairwise = {a+'_beats_'+b: float((rolling[a]>rolling[b]).mean())
        for a in names for b in names if a != b}
    if args.ablation_candidates:
        concentrations, best_months = [], []
        for name in names:
            m = monthly[name]
            logs = np.log1p(m)
            np.testing.assert_allclose(logs.sum(), np.log(navs[name].iloc[-1]/run['initial_cash']), atol=1e-10)
            base_annual = (navs[name].iloc[-1]/run['initial_cash'])**(252/len(navs))-1
            best = m.nlargest(3)
            for date, value in best.items():
                best_months.append({'strategy': name, 'month': str(date.date()), 'return': float(value)})
            neutralized = {}
            for n in [1,3]:
                selected = m.nlargest(n).index
                scenario = m.copy()
                scenario.loc[selected] = 0.
                total = float(np.prod(1+scenario)-1)
                neutralized[f'best_{n}_months_flat_cumulative_return'] = total
                neutralized[f'best_{n}_months_flat_cagr'] = (1+total)**(252/len(navs))-1
            nav = navs[name]
            dd = nav/nav.cummax()-1
            trough = dd.idxmin()
            peak_value = nav.loc[:trough].max()
            peak = nav.loc[:trough][nav.loc[:trough].eq(peak_value)].index[-1]
            recovered = nav.loc[trough:][nav.loc[trough:].ge(peak_value)]
            recovery = str(recovered.index[0].date()) if len(recovered) else None
            concentrations.append({'strategy':name,'months':len(m),
                'positive_months':int(m.gt(1e-12).sum()),'negative_months':int(m.lt(-1e-12).sum()),
                'flat_months':int(m.abs().le(1e-12).sum()),'base_cagr':base_annual,
                'median_monthly_return':float(m.median()),'best_month':str(m.idxmax().date()),
                'best_month_return':float(m.max()),'worst_month':str(m.idxmin().date()),
                'worst_month_return':float(m.min()),
                'best_month_share_of_total_log_growth':float(logs.nlargest(1).sum()/logs.sum()),
                'best_3_months_share_of_total_log_growth':float(logs.nlargest(3).sum()/logs.sum()),
                'max_drawdown':float(dd.min()),'drawdown_peak':str(peak.date()),
                'drawdown_trough':str(trough.date()),'drawdown_recovery':recovery,**neutralized})
        pd.DataFrame(concentrations).to_csv(target/'month_dependency.csv',index=False)
        pd.DataFrame(best_months).to_csv(target/'top_months.csv',index=False)
        # A pre-defined final 12-calendar-month segment versus the preceding sample.
        segments=[]
        split=month_end.index[-13]
        for label, mask in [('earlier_sample', navs.index<=split), ('final_12_months', navs.index>split)]:
            segment=navs.loc[mask]
            prior=navs.loc[navs.index<segment.index[0]]
            start_value=prior.iloc[-1] if len(prior) else run['initial_cash']
            total=segment.iloc[-1]/start_value-1
            for name in names:
                segments.append({'segment':label,'strategy':name,'start':str(segment.index[0].date()),
                    'end':str(segment.index[-1].date()),'trading_days':len(segment),
                    'cumulative_return':float(total[name]),'annualized_return':float((1+total[name])**(252/len(segment))-1)})
        pd.DataFrame(segments).to_csv(target/'final_year_split.csv',index=False)
        reference=OUT/'author_baseline_comparison/time_cost_checks'
        reference_rolling=pd.read_csv(reference/'rolling_12_calendar_month_returns_3bp.csv',index_col=0,parse_dates=True)
        rolling.index = rolling.index.as_unit('ns')
        reference_rolling.index = reference_rolling.index.as_unit('ns')
        assert rolling.index.equals(reference_rolling.index)
        combined=rolling.join(reference_rolling)
        combined.to_csv(target/'rolling_12_month_all_five.csv')
        combined.rank(axis=1,ascending=False,method='min').to_csv(target/'rolling_ranks_all_five.csv')
        reference_cost=pd.read_csv(reference/'cost_sensitivity.csv')
        pd.concat([cost,reference_cost],ignore_index=True).pivot(index='one_way_total_friction_bp',columns='strategy',values='年化收益率').to_csv(target/'cost_cagr_all_five.csv')
        ref_monthly=pd.read_csv(reference/'monthly_returns_3bp.csv',index_col=0,parse_dates=True)
        reference_concentration=[]
        for name in ref_monthly:
            m=ref_monthly[name]
            logs=np.log1p(m)
            flat=m.copy()
            flat.loc[m.nlargest(3).index]=0.
            reference_concentration.append({'strategy':name,
                'best_3_months_share_of_total_log_growth':float(logs.nlargest(3).sum()/logs.sum()),
                'best_3_months_flat_cagr':float(np.prod(1+flat)**(252/len(navs))-1)})
        pd.concat([pd.DataFrame(concentrations)[['strategy','best_3_months_share_of_total_log_growth','best_3_months_flat_cagr']],
                   pd.DataFrame(reference_concentration)],ignore_index=True).to_csv(target/'month_dependency_all_five.csv',index=False)
        write_json(target/'month_dependency_method.json', {
            'log_growth_share':'Top positive monthly log returns / total log growth; additive, may exceed 100% when other months offset gains.',
            'flat_month_scenario':'Replace the best 1 or 3 observed monthly returns with zero, retain all dates and annualization length. Arithmetic sensitivity, not a rerun or executable strategy.',
            'final_year_split':'Last 12 calendar months versus the preceding sample; existing positions carried, no restart.',
            'overlapping_windows':'37 rolling windows overlap; frequencies are descriptive, not independent trials.',
            'no_new_out_of_sample_claim':True})
        print('MONTH DEPENDENCY\n'+pd.DataFrame(concentrations).to_string(index=False),flush=True)
    write_json(target/'validation.json', {'all_cost_case_ledgers_passed': checks,
        'all_base_navs_match_previous_results': True, 'annual_returns_reconcile': True,
        'rolling_returns_reconcile': True, 'rolling_windows': len(rolling)})
    write_json(target/'run.json', dict(run,
        tested_strategies=names,
        friction_grid_bp=[3,5,10,20],
        cost_method='Total one-way friction charged as proportional fees; slippage=0 to avoid double counting. Illustrative scenarios, not calibrated market impact.',
        rolling_method='12 consecutive calendar months, measured at month end; overlapping windows.',
        annual_periods={'2022':'Sep-Dec, partial year', '2023':'full year', '2024':'full year', '2025':'full year', '2026':'Jan-Aug, partial year'},
        purpose='Historical stability analysis on an already inspected sample; no parameter changes or new out-of-sample claim.',
        pairwise_rolling_win_fractions=pairwise))

    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(10, 8))
    for name in names:
        axes[0].plot(rolling.index, rolling[name]*100, label=name)
        part = cost.loc[cost.strategy.eq(name)].sort_values('one_way_total_friction_bp')
        axes[1].plot(part.one_way_total_friction_bp, part['年化收益率']*100, marker='o', label=name)
    axes[0].axhline(0, color='grey', linewidth=.8)
    axes[0].set(title='Historical stability | Same signals, sell before buy', ylabel='Rolling 12-month net return (%)')
    axes[0].legend()
    axes[1].set(xlabel='One-way total friction (bp)', ylabel='Net CAGR (%)', xticks=[3,5,10,20])
    for ax in axes:
        ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(target/'time_cost_comparison.png', dpi=160)
    print('YEARLY\n'+annual.to_string(), flush=True)
    print('ROLLING\n'+stability.to_string(), flush=True)
    print('COST\n'+cost[['strategy','one_way_total_friction_bp','年化收益率','最大回撤','夏普比率']].to_string(index=False), flush=True)
    print('PAIRWISE\n'+json.dumps(pairwise, indent=2), flush=True)


if __name__ == '__main__':
    main()
