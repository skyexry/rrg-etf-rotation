"""Read back a completed RRG run and reconcile its accounting and dates."""
import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from rrg_pipeline import DB, OUT, verify_upstream, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory', type=Path, default=OUT/'citic_base_reproduction')
    args = parser.parse_args()
    target = args.run_directory
    run = json.loads((target/'run.json').read_text())
    input_db = Path(run.get('input_database', DB))
    assert run['upstream_commit'] == verify_upstream()
    w = pd.read_parquet(target/'rrg_weights.parquet')
    observed = w.to_numpy()[~w.isna().to_numpy()]
    assert np.isfinite(observed).all()
    assert w.min().min() >= -1e-8
    assert w.sum(axis=1).max() <= 1+1e-5
    nav_frame = pd.read_csv(target/'RRG_base_nav.csv', index_col=0, parse_dates=True)
    assert nav_frame.shape[1] == 1
    nav = nav_frame.iloc[:, 0]
    assert nav.index.is_monotonic_increasing and nav.index.is_unique
    assert np.isfinite(nav).all() and nav.gt(0).all()
    stats = pd.read_csv(target/'performance.csv', index_col=0).RRG_base
    expected_total = nav.iloc[-1]/run['initial_cash']-1
    expected_annual = (1+expected_total)**(252/len(nav))-1
    expected_mdd = (nav/nav.cummax()-1).min()
    np.testing.assert_allclose(stats['累计收益率'], expected_total, atol=1e-10)
    np.testing.assert_allclose(stats['年化收益率'], expected_annual, atol=1e-10)
    np.testing.assert_allclose(stats['最大回撤'], expected_mdd, atol=1e-10)
    orders = pd.read_csv(target/'RRG_base_orders.csv', parse_dates=['Timestamp'])
    assert len(orders) == stats['总成交笔数'] and len(orders)>0
    assert orders['Size'].gt(0).all() and orders['Price'].gt(0).all()
    np.testing.assert_allclose(orders.Fees, orders.Size*orders.Price*run['fees'], rtol=1e-9, atol=1e-6)
    assert orders.Timestamp.between(pd.Timestamp(run['backtest_start']), pd.Timestamp(run['end'])).all()
    with duckdb.connect(str(input_db), read_only=True) as c:
        meta = c.execute('SELECT ts_code,list_date FROM etf_meta').df()
        dates = pd.to_datetime(c.execute('SELECT trade_date FROM trade_days').df().trade_date)
    meta['list_date'] = pd.to_datetime(meta.list_date, errors='coerce')
    listed = meta.set_index('ts_code').list_date
    assert nav.index.isin(dates).all()
    assert orders.Timestamp.isin(dates).all()
    signed = np.where(orders.Side.eq('Buy'), orders.Size, -orders.Size)
    position_path = pd.Series(signed, index=orders.index).groupby(orders.Column).cumsum()
    assert position_path.min() >= -1e-6
    positions = pd.Series(signed, index=orders.index).groupby(orders.Column).sum()
    cash = run['initial_cash']-np.sum(signed*orders.Price)-orders.Fees.sum()
    assert cash >= -1e-5
    with duckdb.connect(str(input_db), read_only=True) as c:
        last = c.execute('''SELECT code,adjclose FROM etf_daily WHERE adjclose>0
            QUALIFY row_number() OVER (PARTITION BY code ORDER BY date DESC)=1''').df().set_index('code').adjclose
    ending_equity = cash+(positions*last.reindex(positions.index)).sum()
    np.testing.assert_allclose(ending_equity,nav.iloc[-1],rtol=1e-9,atol=1e-5)
    for code in w.columns:
        active = w.index[w[code]>1e-8]
        if len(active):
            assert pd.notna(listed[code]) and (active>=listed[code]).all()
    before_backtest = w.loc[w.index<pd.Timestamp(run['backtest_start'])]
    assert not before_backtest.empty
    assert orders.Timestamp.min()>pd.Timestamp(run['backtest_start'])
    reference = run['reference']
    differences = {'annualized_return_pp': (expected_annual-reference['annualized_return'])*100,
        'total_return_pp': (expected_total-reference['total_return'])*100,
        'max_drawdown_pp': (expected_mdd-reference['max_drawdown'])*100}
    result = {'status':'passed_result_readback_not_live_trading_certification',
        'upstream_source_unchanged':True, 'positive_finite_nav':True,
        'observed_target_weights_long_only_and_bounded':True, 'fees_reconcile':True,
        'orders_cash_and_final_positions_reconcile':True,
        'upstream_nan_weight_cells':int(w.isna().sum().sum()),
        'upstream_nan_weight_semantics':'No instruction; upstream engine keeps existing shares. Preserved, not filled with zero.',
        'performance_reconciles_to_nav':True, 'listing_and_trading_dates_valid':True,
        'trading_days':len(nav), 'orders':len(orders), 'first_order':str(orders.Timestamp.min().date()),
        'initial_cash_cny':run['initial_cash'], 'ending_cash_and_holdings_cny':float(nav.iloc[-1]),
        'total_fees_cny':float(orders.Fees.sum()), 'reference_differences':differences}
    write_json(target/'validation.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
