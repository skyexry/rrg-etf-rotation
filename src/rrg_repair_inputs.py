"""Audit supplemental membership, repair ETF identities, and stage new inputs."""
from pathlib import Path
import json
import shutil

import duckdb
import pandas as pd

from rrg_pipeline import ROOT, write_json, bind_upstream, EXCLUDE
from rrg_report_tushare import DB as OLD_DB, OUT as OLD_OUT, PARENT, START, END, WARMUP, sha
from rrg_repair_download import DATA, RAW

OUT = ROOT/'results/rrg_tushare_repaired_20261107'
DB = OUT/'inputs.duckdb'


def metadata():
    frames = [pd.read_parquet(RAW/f'metadata/etf_{s}.parquet') for s in ['L', 'D']]
    all_meta = pd.concat(frames, ignore_index=True)
    all_meta['base_code'] = all_meta.ts_code.str[:6]
    meta = all_meta.loc[all_meta.ts_code.str.fullmatch(r'\d{6}\.(SH|SZ)')].copy()
    assert not meta.ts_code.duplicated().any()
    aliases = all_meta.loc[all_meta.ts_code.str.fullmatch(r'\d{6}\.OF')]
    fixes = []
    for field in ['index_code', 'index_name', 'list_date', 'setup_date']:
        valid = aliases.dropna(subset=[field])
        assert valid.groupby('base_code')[field].nunique().le(1).all()
        values = valid.drop_duplicates('base_code').set_index('base_code')[field]
        mapped = meta.base_code.map(values)
        change = meta[field].isna() & mapped.notna()
        for row in meta.loc[change].itertuples():
            fixes.append({'ts_code': row.ts_code, 'field': field,
                          'value': mapped.loc[row.Index], 'source': row.base_code+'.OF'})
        meta[field] = meta[field].fillna(mapped)
    pd.DataFrame(fixes).to_csv(OUT/'etf_identity_repairs.csv', index=False)
    funds = pd.concat([pd.read_parquet(RAW/f'metadata/funds_{s}.parquet') for s in ['L', 'D']])
    funds = funds.loc[funds.ts_code.str.fullmatch(r'\d{6}\.(SH|SZ|OF)')].copy()
    funds['base_code'] = funds.ts_code.str[:6]
    valid = funds.dropna(subset=['delist_date'])
    assert valid.groupby('base_code').delist_date.nunique().le(1).all()
    meta['delist_date'] = meta.base_code.map(valid.drop_duplicates('base_code').set_index('base_code').delist_date)
    indexes = pd.concat([pd.read_parquet(f) for f in sorted((RAW/'metadata').glob('index_*.parquet'))])
    assert indexes.groupby('ts_code').category.nunique().le(1).all()
    indexes = indexes.drop_duplicates('ts_code')
    meta['index_category'] = meta.index_code.map(indexes.set_index('ts_code').category)
    meta['category_source'] = 'exact_index_code'
    # Tushare ETF metadata uses .CNI while index_basic's SZSE catalog uses .SZ.
    # Resolve only this documented code-suffix alias; no name guessing.
    cni = meta.index_category.isna() & meta.index_code.fillna('').str.endswith('.CNI')
    alternate = meta.loc[cni, 'index_code'].str.replace(r'\.CNI$', '.SZ', regex=True)
    cats = alternate.map(indexes.set_index('ts_code').category)
    meta.loc[cni, 'index_category'] = cats
    meta.loc[cni & meta.index_category.notna(), 'category_source'] = 'CNI_to_SZ_code_alias'
    eligible_type = meta.index_category.fillna('').str.contains('行业|主题', regex=True)
    meta['eligible_type'] = eligible_type
    meta['candidate_reason'] = 'classified_other_index_type'
    meta.loc[meta.index_category.isna(), 'candidate_reason'] = 'index_category_unavailable'
    meta.loc[eligible_type, 'candidate_reason'] = 'industry_or_theme'
    meta.loc[meta.etf_type.eq('QDII'), 'candidate_reason'] = 'QDII'
    for field in ['list_date', 'setup_date', 'delist_date']:
        meta[field] = pd.to_datetime(meta[field], format='%Y%m%d')
    meta.loc[meta.list_date.isna()|meta.list_date.gt(pd.Timestamp(END)), 'candidate_reason'] = 'not_listed_by_end'
    meta.loc[meta.delist_date.le(pd.Timestamp(START)), 'candidate_reason'] = 'delisted_before_backtest'
    meta['eligible'] = meta.candidate_reason.eq('industry_or_theme')
    meta.to_parquet(OUT/'etf_metadata_audit.parquet', index=False)
    meta.to_csv(OUT/'etf_metadata_audit.csv', index=False)
    return meta


def coverage(con):
    return con.execute('''SELECT year(a.date) AS year,count(*) AS stock_days,
        count(m.stock_code) AS classified_stock_days,
        count(m.stock_code)*1.0/count(*) AS stock_day_coverage,
        sum(CASE WHEN m.stock_code IS NOT NULL THEN f.float_mv ELSE 0 END)
            /sum(f.float_mv) AS free_float_value_coverage
        FROM ashares_close a LEFT JOIN zx_membership m
        ON a.date=m.trade_date AND a.stock_code=m.stock_code
        LEFT JOIN ashares_float_mv f ON a.date=f.date AND a.stock_code=f.stock_code
        GROUP BY 1 ORDER BY 1''').df()


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = metadata()
    with duckdb.connect(str(OLD_DB), read_only=True) as con:
        price_codes = set(con.execute('SELECT DISTINCT code FROM etf_daily').df().code)
        old_coverage = coverage(con)
        availability = con.execute('''SELECT code,min(date) AS first_quote,max(date) AS last_quote,
            count(*) AS quote_count FROM etf_daily GROUP BY 1''').df()
    meta['has_prices'] = meta.ts_code.isin(price_codes)
    missing = meta.loc[meta.eligible & ~meta.has_prices]
    missing.to_csv(OUT/'missing_candidate_prices.csv', index=False)
    # Additional history has explicit in/out dates. Current queries yielded no
    # new intervals; fail rather than silently skipping a future successful repair.
    history = json.loads((DATA/'history_query_audit.json').read_text())
    assert history['additional_intervals']==0, 'New dated intervals require rebuilding zx_membership.'
    old_coverage.to_csv(OUT/'historical_membership_coverage.csv', index=False)
    meta.merge(availability, left_on='ts_code', right_on='code', how='left').to_csv(OUT/'etf_quote_coverage.csv', index=False)
    if not DB.exists():
        temporary = DB.with_suffix('.building.duckdb')
        shutil.copy2(OLD_DB, temporary)
        with duckdb.connect(str(temporary)) as con:
            cols = con.execute('DESCRIBE etf_meta').df().column_name.tolist()
            con.register('_meta', meta[cols])
            con.execute('DELETE FROM etf_meta')
            con.execute('INSERT INTO etf_meta SELECT * FROM _meta')
        temporary.replace(DB)
    write_json(OUT/'input_audit.json', {
        'old_input_sha256': sha(OLD_DB), 'new_input_sha256': sha(DB),
        'historical_membership_queries': history,
        'classification': 'Tushare index_basic industry/theme category; explicit CNI to SZ suffix alias only.',
        'current_classification_labels': 'Historical product/index label changes unavailable; current catalog labels retained.',
        'identity_field_repairs': len(pd.read_csv(OUT/'etf_identity_repairs.csv')),
        'candidate_etfs': int(meta.eligible.sum()),
        'delisted_candidates': int((meta.eligible & meta.list_status.eq('D')).sum()),
        'candidate_price_codes_missing': missing.ts_code.tolist(),
        'old_files_preserved': True})
    print('Metadata staged:', int(meta.eligible.sum()), 'industry/theme ETFs,',
          int((meta.eligible & meta.list_status.eq('D')).sum()), 'currently delisted.', flush=True)


def weights():
    bind_upstream(DB)
    from src.data_provider import fetch_etf_meta, fetch_etf_daily
    from src.backtest import build_report_universe_panel, build_overseas_share, build_exposure_panel, mask_listed
    from src.exposure import ETFExposureBuilder
    from src.store import RRGStore
    from src.optimizer import batch_optimize
    from src.strategy import select_by_quadrant, select_by_diffusion, select_diffusion_with_rrg
    meta_all = pd.read_parquet(OUT/'etf_metadata_audit.parquet').set_index('ts_code')
    meta = fetch_etf_meta(only_listed=False, drop_qdii=True)
    meta = meta.loc[meta.index.intersection(meta_all.index[meta_all.eligible])]
    close, open_, amount = fetch_etf_daily(list(meta.index), START, END, lookback=25)
    with RRGStore(DB) as store:
        with ETFExposureBuilder(list(meta.index), START, END, store=store, classification='zx') as builder:
            iw = builder.build_industry_weights()
            overseas = build_overseas_share(builder.load_portfolio())
    iw.to_parquet(OUT/'visible_etf_industry_weights.parquet')
    ratio = pd.read_parquet(PARENT/'rs_ratio.parquet')
    mom = pd.read_parquet(PARENT/'rs_momentum.parquet')
    prior = pd.read_parquet(OLD_OUT/'industry_selection.parquet')
    dates = prior.index
    panel = build_report_universe_panel(meta, amount, dates, lookback=20,
                                       overseas_share=overseas, overseas_threshold=.5)
    # Apply historical listing and retirement intervals at each formation date.
    for d in dates:
        vals = panel.loc[d]
        for index, code in vals.dropna().items():
            listed = meta_all.loc[code, 'list_date'] <= d
            retired = pd.notna(meta_all.loc[code, 'delist_date']) and meta_all.loc[code, 'delist_date'] <= d
            observed = code in close and d in close.index and pd.notna(close.loc[d, code])
            if not listed or retired or not observed:
                panel.loc[d, index] = None
    exposures = build_exposure_panel(panel, iw)
    assert set(dates).issubset(exposures), 'Some monthly dates lack exposures.'
    panel.to_parquet(OUT/'candidate_etf_panel.parquet')
    # Since no dated industry intervals were added, both the RRG and diffusion
    # signals must remain identical; reuse verified inputs and check explicitly.
    breadth = pd.read_parquet(OLD_OUT/'free_float_diffusion.parquet')
    combined = select_diffusion_with_rrg(breadth, ratio, mom, top_n=6, keep_quadrants=(1,2)).loc[dates].astype(float)
    pd.testing.assert_frame_equal(combined, prior, check_freq=False, check_names=False)
    with duckdb.connect(str(DB), read_only=True) as con:
        history = con.execute('SELECT trade_date,code,close FROM zx_industry_daily ORDER BY 1,2').df()
    industry = history.pivot(index='trade_date', columns='code', values='close').drop(columns=EXCLUDE)
    assert industry.index.min() <= pd.Timestamp(WARMUP), 'Momentum needs the unchanged 2017 warmup.'
    scores = {'Pure_RRG_Q1': select_by_quadrant(ratio,mom,quadrants=(1,),top_n=6).loc[dates].astype(float),
              'Report_base': combined,
              'Industry_momentum_220_6': select_by_diffusion(industry.pct_change(220,fill_method=None),top_n=6).loc[dates].astype(float)}
    candidates = []
    for d,E in exposures.items():
        candidates.extend({'date': d, 'code': c, 'currently_delisted': meta_all.loc[c,'list_status']=='D'} for c in E.index)
    pd.DataFrame(candidates).to_csv(OUT/'effective_candidate_history.csv', index=False)
    for name, sig in scores.items():
        print('Optimizing', name, flush=True)
        w = mask_listed(batch_optimize(sig, exposures=exposures, dates=dates,preset='base',strict=True),meta)
        sig.to_parquet(OUT/f'{name}_signals.parquet')
        w.fillna(0.).to_parquet(OUT/f'{name}_weights.parquet')
    ew = pd.DataFrame({d:pd.Series(1/len(exposures[d]),index=exposures[d].index) for d in dates}).T
    ew.fillna(0.).to_parquet(OUT/'ETF_equal_weight_weights.parquet')
    write_json(OUT/'weight_build.json', {
        'new_input_sha256': sha(DB), 'months':len(dates),'same_sector_signals_as_prior':True,
        'monthly_candidate_count':pd.Series({str(d):len(E) for d,E in exposures.items()}).to_dict(),
        'delisted_effective_candidates':sorted(set(c['code'] for c in candidates if c['currently_delisted']))})
    print('Four target weight sets complete.',flush=True)


if __name__=='__main__':
    prepare()
    weights()
