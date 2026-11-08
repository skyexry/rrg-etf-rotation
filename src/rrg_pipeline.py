"""Adapt local inputs to the unmodified QuantsPlaybook RRG pipeline.

build --preview: interface/coverage checks while the download is running.
signals: run original industry signal code, without publishing portfolio returns.
reproduce: run the original CITIC/base notebook path once inputs are complete.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor/quantsplaybook_rrg'
DATA = ROOT / 'data/rrg'
RAW = DATA / 'raw'
DB = DATA / 'rrg_inputs.duckdb'
REPORT = DATA / 'snapshot_report.json'
OUT = ROOT / 'results/rrg'
EXCLUDE = ['CI005029.CI', 'CI005030.CI']
START, END, SIGNAL_START, BACKTEST_START = '2019-12-02', '2026-08-31', '2021-01-01', '2022-09-01'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def verify_upstream():
    manifest = json.loads((VENDOR / 'UPSTREAM.json').read_text())
    for item in manifest['files']:
        blob = (VENDOR / item['path']).read_bytes()
        digest = hashlib.sha1(b'blob ' + str(len(blob)).encode() + b'\0' + blob).hexdigest()
        if digest != item['git_blob_sha']:
            raise ValueError(f'Upstream file changed: {item["path"]}')
    return manifest['commit']


def bind_upstream(database=DB):
    verify_upstream()
    sys.path.insert(0, str(VENDOR))
    from src import data_provider
    # This is the data-path configuration only; vendor files stay byte-identical.
    data_provider.DEFAULT_DB_PATH = str(database)


def checked_raw(path):
    marker = path.with_suffix('.json')
    if not path.exists() or not marker.exists():
        raise FileNotFoundError(path)
    meta = json.loads(marker.read_text())
    if hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']:
        raise ValueError(f'Raw checksum mismatch: {path}')
    return pd.read_parquet(path)


def sql_path(path):
    return "'" + str(path).replace("'", "''") + "'"


def resolve_member_dates(members):
    """Three documented overlaps: retain old classification until its exit.

    Research convention, not a claim that the individual official notices were
    recovered. Raw intervals and all vendor algorithm files remain unchanged.
    """
    result = members.copy()
    changes = []
    for code in ['000615.SZ', '000990.SZ', '002254.SZ']:
        new = result.ts_code.eq(code) & result.in_date.eq(pd.Timestamp('2022-12-13'))
        old = result.ts_code.eq(code) & result.out_date.eq(pd.Timestamp('2023-01-03'))
        if new.sum() != 1 or old.sum() != 1:
            raise ValueError(f'Industry conflict inputs changed for {code}; review policy')
        changes.append({'ts_code': code, 'industry_code': result.loc[new, 'l1_code'].iloc[0],
            'raw_in_date': '2022-12-13', 'research_in_date': '2023-01-03',
            'basis': 'documented research convention: old interval exit date'})
        result.loc[new, 'in_date'] = pd.Timestamp('2023-01-03')
    return result, pd.DataFrame(changes)


def baseline_weights(panel, close, *, lookback=None, top_n=None):
    """Only construct baseline weights; use upstream engine for all execution.

    None/None = equal weight. Momentum parameters must both be supplied.
    Momentum ties use code order; insufficient history remains unselected.
    """
    if (lookback is None) != (top_n is None):
        raise ValueError('Specify both momentum lookback and top_n')
    if lookback is not None and (lookback < 1 or top_n < 1):
        raise ValueError('Momentum lookback and top_n must be positive')
    rows = {}
    momentum = close.pct_change(periods=lookback, fill_method=None) if lookback else None
    for date, row in panel.iterrows():
        codes = sorted(set(row.dropna()) & set(close.columns))
        codes = [c for c in codes if date in close.index and pd.notna(close.at[date, c]) and close.at[date, c] > 0]
        if momentum is not None:
            scores = momentum.loc[date, codes].dropna().sort_index()
            codes = scores.sort_values(ascending=False, kind='stable').head(top_n).index.tolist()
        rows[date] = pd.Series(1. / len(codes), index=codes, dtype=float) if codes else pd.Series(dtype=float)
    return pd.DataFrame(rows).T.fillna(0.).sort_index()


def build_snapshot(preview=False):
    verify_upstream()
    plan = json.loads((DATA / 'download_plan.json').read_text())
    required = [t for t in plan['tasks'] if t['api'] != 'fund_portfolio'
        or t['params']['ts_code'].endswith(('.SH', '.SZ'))]
    missing = [t['file'] for t in required if not (RAW / t['file']).exists()
        or not (RAW / t['file']).with_suffix('.json').exists()]
    if missing and not preview:
        raise RuntimeError(f'{len(missing)} required inputs pending; use --preview for interface work')
    # Preserve all raw identifiers. Only exchange codes enter the tradeable table.
    meta = pd.concat([checked_raw(RAW / f'metadata/etf_{s}.parquet') for s in ['L', 'D', 'P']], ignore_index=True)
    meta = meta.loc[meta.ts_code.str.endswith(('.SH', '.SZ'))].copy()
    if meta.ts_code.duplicated().any():
        raise ValueError('Duplicate exchange ETF metadata')
    members = pd.concat([checked_raw(RAW / f'metadata/ci_members_{s}.parquet') for s in ['Y', 'N']], ignore_index=True)
    members = members.drop_duplicates(['ts_code', 'l1_code', 'in_date', 'out_date'])
    for col in ['in_date', 'out_date']:
        members[col] = pd.to_datetime(members[col], format='%Y%m%d', errors='raise')
    members, date_changes = resolve_member_dates(members)
    date_changes.to_csv(DATA/'industry_date_conventions.csv', index=False)
    industries = pd.concat([checked_raw(f) for f in sorted((RAW / 'industry_daily').glob('*.parquet'))], ignore_index=True)
    names = members.set_index('l1_code').l1_name.to_dict()
    industries = industries.rename(columns={'ts_code': 'code'})
    industries['industry_name'] = industries.code.map(names)
    industries['trade_date'] = pd.to_datetime(industries.trade_date, format='%Y%m%d')
    industries = industries.loc[industries.trade_date.between(START, END), ['trade_date', 'code', 'industry_name', 'close', 'open']]
    cal = checked_raw(RAW / 'metadata/calendar_SSE.parquet')
    days = pd.DataFrame({'trade_date': pd.to_datetime(cal.loc[cal.is_open.eq(1), 'cal_date'], format='%Y%m%d')})
    days = days.loc[days.trade_date.between(START, END)].sort_values('trade_date')
    temporary = DB.with_suffix('.building.duckdb')
    temporary.unlink(missing_ok=True)
    con = duckdb.connect(str(temporary))
    con.execute('SET threads=2')
    bind_upstream()
    from src.store import RRGStore
    # Schema from upstream, then fill it from our audited raw files.
    with RRGStore(temporary, read_only=False):
        pass
    tables = {'trade_days': days, 'etf_meta': meta, 'zx_industry_daily': industries}
    for name, frame in tables.items():
        con.register('_frame', frame)
        con.execute(f'CREATE TABLE {name} AS SELECT * FROM _frame')
    con.register('_members', members)
    con.execute('''CREATE TABLE _membership_expanded AS
        SELECT DISTINCT d.trade_date, m.ts_code AS stock_code, m.l1_code AS industry_code
        FROM trade_days d JOIN _members m ON d.trade_date >= m.in_date
        AND (m.out_date IS NULL OR d.trade_date < m.out_date)''')
    overlap = con.execute('''SELECT count(*) FROM (SELECT trade_date, stock_code
        FROM _membership_expanded GROUP BY ALL HAVING count(*) > 1)''').fetchone()[0]
    if overlap:
        conflicts = con.execute('''SELECT * FROM _membership_expanded
            WHERE (trade_date, stock_code) IN (SELECT trade_date,stock_code
            FROM _membership_expanded GROUP BY ALL HAVING count(*)>1)
            ORDER BY trade_date, stock_code, industry_code''').df()
        conflicts.to_csv(DATA/'ambiguous_industry_stock_days.csv', index=False)
        if not preview:
            con.close()
            raise ValueError(f'{overlap} ambiguous stock/day industry assignments; inspect intervals')
    # Preview keeps only unambiguous observations; formal build requires resolution.
    con.execute('''INSERT INTO zx_membership SELECT * FROM _membership_expanded
        QUALIFY count(*) OVER (PARTITION BY trade_date,stock_code)=1''')
    con.execute('DROP TABLE _membership_expanded')
    con.execute('INSERT INTO zx_membership_coverage VALUES (1, ?, ?, now())', [START, END])
    # Existing cleaned equity files already carry explicit currency/share units.
    equity_paths = sorted(f for f in (ROOT / 'data/clean/daily').glob('*.parquet')
        if '201912' <= f.stem <= '202608')
    if len(equity_paths) != 81:
        raise ValueError(f'Expected 81 equity month files, got {len(equity_paths)}')
    path_list = '[' + ','.join(sql_path(f) for f in equity_paths) + ']'
    con.execute(f'''CREATE TABLE ashares_close AS SELECT trade_date AS date,
        ts_code AS stock_code, close FROM read_parquet({path_list}, union_by_name=true)
        WHERE trade_date BETWEEN DATE '{START}' AND DATE '{END}' ''')
    con.execute(f'''CREATE TABLE ashares_float_mv AS SELECT trade_date AS date,
        ts_code AS stock_code, float_market_cap_cny AS float_mv
        FROM read_parquet({path_list}, union_by_name=true)
        WHERE trade_date BETWEEN DATE '{START}' AND DATE '{END}' ''')
    # Match the published QuantsPlaybook snapshot's float-market-cap proxy.
    # A true free-float version is a distinct extension, not a silent replacement.
    for api in ['fund_daily', 'fund_adj']:
        files = sorted((RAW / api).glob('*.parquet'))
        for f in files:
            checked_raw(f)
        paths = '[' + ','.join(sql_path(f) for f in files) + ']'
        con.execute(f'CREATE TEMP TABLE _{api} AS SELECT * FROM read_parquet({paths}, union_by_name=true)')
    duplicate = con.execute('''SELECT count(*) FROM (SELECT ts_code,trade_date FROM _fund_daily
        GROUP BY ALL HAVING count(*)>1)''').fetchone()[0]
    if duplicate:
        raise ValueError('Duplicate fund price keys')
    missing_adj = con.execute('''SELECT count(*) FROM _fund_daily p JOIN etf_meta m USING(ts_code)
        LEFT JOIN _fund_adj a USING(ts_code,trade_date)
        WHERE a.adj_factor IS NULL OR a.adj_factor<=0''').fetchone()[0]
    con.execute(f'''CREATE TABLE etf_daily AS SELECT strptime(p.trade_date,'%Y%m%d') AS date,
        p.ts_code AS code, p.close*a.adj_factor AS adjclose, p.open*a.adj_factor AS adjopen,
        p.amount*1000 AS amount FROM _fund_daily p JOIN _fund_adj a USING(ts_code,trade_date)
        JOIN etf_meta m USING(ts_code) WHERE a.adj_factor>0
        AND strptime(p.trade_date,'%Y%m%d') BETWEEN DATE '{START}' AND DATE '{END}' ''')
    con.execute('''CREATE TABLE etf_price_missing_adjustment AS SELECT p.* FROM _fund_daily p
        JOIN etf_meta m USING(ts_code) LEFT JOIN _fund_adj a USING(ts_code,trade_date)
        WHERE a.adj_factor IS NULL OR a.adj_factor<=0''')
    port_rows = 0
    processed_codes = []
    revisions_collapsed = 0
    from src.data_provider import _dedup_latest_announcement, _PORTFOLIO_COLS
    for task in required:
        if task['api'] != 'fund_portfolio':
            continue
        file = RAW / task['file']
        if not file.exists() or not file.with_suffix('.json').exists():
            continue
        frame = checked_raw(file)
        code = task['params']['ts_code']
        processed_codes.append(code)
        if frame.empty:
            continue
        frame = frame.rename(columns={'ts_code': 'code'})
        for col in ['ann_date', 'end_date']:
            frame[col] = pd.to_datetime(frame[col], format='%Y%m%d').astype('datetime64[ns]')
        before = len(frame)
        frame = _dedup_latest_announcement(frame)
        revisions_collapsed += before-len(frame)
        con.register('_port', frame[_PORTFOLIO_COLS])
        con.execute('INSERT INTO etf_portfolio SELECT * FROM _port')
        port_rows += len(frame)
    # Watermarks certify queried files, including genuine empty responses.
    # They do not assert that the source contains a disclosure for every period.
    cov = pd.MultiIndex.from_product([processed_codes, pd.date_range(START, END, freq='QE-DEC')], names=['code', 'period']).to_frame(index=False)
    con.register('_cov', cov)
    con.execute('INSERT INTO etf_portfolio_coverage SELECT code,period,current_timestamp FROM _cov')
    coverage = con.execute('''SELECT year(a.date) AS year, count(*) AS observed_stock_days,
        count(m.industry_code) AS classified_stock_days FROM ashares_close a
        LEFT JOIN zx_membership m ON a.date=m.trade_date AND a.stock_code=m.stock_code
        WHERE a.stock_code LIKE '%.SH' OR a.stock_code LIKE '%.SZ' GROUP BY 1 ORDER BY 1''').df()
    coverage['classified_fraction'] = coverage.classified_stock_days/coverage.observed_stock_days
    counts = {t: con.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in
        ['trade_days', 'zx_industry_daily', 'zx_membership', 'ashares_close', 'etf_meta', 'etf_daily', 'etf_portfolio']}
    con.close()
    temporary.replace(DB)
    report = {'status': 'partial_inputs_preview' if missing else 'inputs_collected_pending_research_audit',
        'complete_required_files': not missing, 'pending_files': missing, 'table_rows': counts,
        'ambiguous_industry_stock_days': overlap, 'preview_mode': preview,
        'holdings_files': len(processed_codes), 'holdings_rows': port_rows,
        'missing_adjustment_rows': missing_adj, 'historical_industry_coverage': coverage.to_dict('records'),
        'upstream_commit': verify_upstream(), 'start': START, 'end': END,
        'data_conventions': {'membership_interval': 'in_date <= day < out_date; null out_date is open-ended',
            'diffusion_weight': 'float_market_cap_cny; matches upstream snapshot proxy',
            'prices_for_diffusion': 'original unadjusted close; matches upstream reader',
            'etf_price': 'raw price * same-date fund_adj', 'amount': 'raw amount * 1000 CNY',
            'disclosure_revisions': 'upstream latest-announcement dedup; all vintages remain in raw'},
        'disclosure_revision_rows_collapsed': revisions_collapsed,
        'industry_date_conventions': date_changes.to_dict('records'),
        'limitations': ['Current ETF metadata and point-in-time industry definitions require review.',
            'Fund quarter-end holdings may disclose only largest positions.',
            'Same code snapshot differs from the author database; numeric equality is not assumed.']}
    write_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, default=str), flush=True)


def signal_preview():
    bind_upstream()
    from src.generator import RRGSignalGenerator
    from src.utils import month_end_dates
    gen = RRGSignalGenerator(start_date=SIGNAL_START, end_date=END, classification='zx')
    mask = gen.generate('diffusion_rrg', top_n=6, keep_quadrants=(1, 2), exclude=EXCLUDE)
    ratio, mom = gen.get_rrg()
    target = OUT / 'signal_preview'
    target.mkdir(parents=True, exist_ok=True)
    mask.to_parquet(target/'daily_industry_selection.parquet')
    mask.loc[month_end_dates(mask.index)].to_csv(target/'month_end_selection.csv')
    ratio.to_parquet(target/'rs_ratio.parquet')
    mom.to_parquet(target/'rs_momentum.parquet')
    write_json(target/'status.json', {'status': 'signal_interface_preview_only',
        'portfolio_returns_generated': False, 'rows': len(mask), 'industries': len(mask.columns),
        'selected_daily_max': int(mask.sum(axis=1).max()), 'upstream_commit': verify_upstream()})
    print('Original signal pipeline executed; portfolio returns not generated', flush=True)


def reproduce(with_baselines=False, momentum_lookback=None, momentum_top_n=None, database=None):
    external = database is not None
    input_db = Path(database).expanduser().resolve() if external else DB
    if external:
        if not input_db.is_file():
            raise FileNotFoundError(input_db)
        if input_db == DB.resolve():
            raise ValueError('Use the default run for our own database; external replay expects a separate snapshot')
        with duckdb.connect(str(input_db), read_only=True) as con:
            tables = set(con.execute('SHOW TABLES').df().name)
        required = {'trade_days', 'zx_industry_daily', 'zx_membership',
            'zx_membership_coverage', 'ashares_close', 'ashares_float_mv',
            'etf_meta', 'etf_daily', 'etf_portfolio', 'etf_portfolio_coverage'}
        if not required.issubset(tables):
            raise ValueError(f'External snapshot tables missing: {sorted(required-tables)}')
    else:
        report = json.loads(REPORT.read_text())
        if not report['complete_required_files']:
            raise RuntimeError('Holdings download pending: only signal preview can run')
        if report['preview_mode'] or report['ambiguous_industry_stock_days']:
            raise RuntimeError('Build and review a complete snapshot before portfolio reproduction')
    if with_baselines and (momentum_lookback is None or momentum_top_n is None):
        raise ValueError('Fix baseline lookback and top_n explicitly before running')
    bind_upstream(input_db)
    from src.data_provider import fetch_etf_meta, fetch_etf_daily
    from src.generator import RRGSignalGenerator
    from src.exposure import ETFExposureBuilder
    from src.store import RRGStore
    from src.backtest import (build_overseas_share, build_report_universe_panel,
        build_exposure_panel, mask_listed, run_vbt_backtest, portfolio_stats)
    from src.optimizer import batch_optimize
    from src.utils import month_end_dates
    meta = fetch_etf_meta(only_listed=True, drop_qdii=True)
    # Metadata were retrieved in November; ETFs first listed after the replay
    # endpoint have no investable history in this experiment. Remove before the
    # exposure loader requests their (deliberately undownloaded) future coverage.
    excluded_post_endpoint = []
    if not external:
        post_endpoint = meta.list_date.gt(pd.Timestamp(END))
        excluded_post_endpoint = meta.loc[post_endpoint].reset_index().to_dict('records')
        meta = meta.loc[~post_endpoint]
    codes = sorted(meta.index)
    close, open_, amount = fetch_etf_daily(codes, SIGNAL_START, END, lookback=25)
    gen = RRGSignalGenerator(start_date=SIGNAL_START, end_date=END, classification='zx')
    mask = gen.generate('diffusion_rrg', top_n=6, keep_quadrants=(1, 2), exclude=EXCLUDE)
    me = month_end_dates(mask.index)
    scores = mask.loc[me].astype(float)
    with RRGStore(input_db) as store:
        with ETFExposureBuilder(codes, SIGNAL_START, END, store=store, classification='zx') as builder:
            iw = builder.build_industry_weights()
            overseas = build_overseas_share(builder.load_portfolio())
    panel = build_report_universe_panel(meta, amount, me, lookback=20, overseas_share=overseas, overseas_threshold=.5)
    exposures = build_exposure_panel(panel, iw)
    dates = [d for d in me if d in exposures]
    weights = mask_listed(batch_optimize(scores.loc[dates], exposures=exposures,
        dates=dates, preset='base', strict=False), meta)
    target = OUT / ('author_snapshot_replay' if external else 'citic_base_reproduction')
    target.mkdir(parents=True, exist_ok=True)
    weights.to_parquet(target/'rrg_weights.parquet')
    scores.to_parquet(target/'industry_selection.parquet')
    panel.to_parquet(target/'candidate_etf_panel.parquet')
    weight_sets = {'RRG_base': weights}
    if with_baselines:
        weight_sets['ETF_equal_weight'] = baseline_weights(panel, close)
        weight_sets['ETF_momentum'] = baseline_weights(panel, close, lookback=momentum_lookback, top_n=momentum_top_n)
    stats = {}
    for name, w in weight_sets.items():
        common = w.columns.intersection(close.columns)
        pf = run_vbt_backtest(w[common].loc[BACKTEST_START:], close[common].loc[BACKTEST_START:],
            open_[common].loc[BACKTEST_START:], fees=3e-4, slippage=0., init_cash=1e7)
        stats[name] = portfolio_stats(pf)
        pf.value().to_csv(target/f'{name}_nav.csv')
        pf.orders.records_readable.to_csv(target/f'{name}_orders.csv', index=False)
    pd.DataFrame(stats).to_csv(target/'performance.csv')
    digest = hashlib.sha256()
    with input_db.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(block)
    write_json(target/'run.json', {'status': 'external_snapshot_replay' if external else 'own_data_upstream_code_reproduction',
        'input_database': str(input_db), 'input_database_sha256': digest.hexdigest(),
        'upstream_commit': verify_upstream(), 'backtest_start': BACKTEST_START, 'end': END,
        'fees': .0003, 'slippage': 0., 'initial_cash': 1e7,
        'reference': {'annualized_return': .125841, 'total_return': .576655, 'max_drawdown': -.177532},
        'with_baselines': with_baselines, 'momentum_lookback': momentum_lookback,
        'momentum_top_n': momentum_top_n, 'snapshot_report': None if external else str(REPORT),
        'local_industry_date_overrides_applied': not external,
        'post_endpoint_metadata_filter_applied': not external})
    write_json(target/'post_endpoint_etfs.json', excluded_post_endpoint)
    print(pd.DataFrame(stats).to_string(), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['build', 'signals', 'reproduce'])
    p.add_argument('--preview', action='store_true')
    p.add_argument('--with-baselines', action='store_true')
    p.add_argument('--momentum-lookback', type=int)
    p.add_argument('--momentum-top-n', type=int)
    p.add_argument('--database', type=Path, help='Read-only author snapshot; output goes to a separate replay directory')
    a = p.parse_args()
    if a.action == 'build':
        build_snapshot(a.preview)
    elif a.action == 'signals':
        signal_preview()
    else:
        reproduce(a.with_baselines, a.momentum_lookback, a.momentum_top_n, a.database)


if __name__ == '__main__':
    main()
