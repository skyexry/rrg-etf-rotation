"""Supplement the existing raw RRG inputs without replacing earlier snapshots."""
from pathlib import Path
import json

import pandas as pd

import download_rrg as download

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/rrg/repair_20261107'
RAW = DATA / 'raw'
OLD = ROOT / 'data/rrg/raw'


def main():
    download.DATA, download.RAW = DATA, RAW
    tasks = []
    for status in ['L', 'D']:
        tasks.append(('etf_basic', {'list_status': status}, f'metadata/etf_{status}.parquet', 5000))
        tasks.append(('fund_basic', {'market': 'E', 'status': status}, f'metadata/funds_{status}.parquet', 5000))
    for market in ['CSI', 'SSE', 'SZSE', 'OTH', 'CICC', 'MSCI', 'SW']:
        tasks.append(('index_basic', {'market': market}, f'metadata/index_{market}.parquet', 5000))
    tasks.append(('etf_index', {}, 'metadata/etf_index.parquet', 5000))
    for current in ['Y', 'N']:
        tasks.append(('ci_index_member', {'is_new': current}, f'membership/global_{current}.parquet', 5000))
    download.run_tasks(tasks, 4, 'refresh_metadata')
    members = pd.concat([pd.read_parquet(OLD/f'metadata/ci_members_{s}.parquet') for s in ['Y', 'N']])
    industries = sorted(members.l1_code.unique())
    tasks = [('ci_index_member', {'l1_code': code, 'is_new': current},
              f'membership/{code}_{current}.parquet', 5000)
             for code in industries for current in ['Y', 'N']]
    download.run_tasks(tasks, 4, 'industry_partition_history')
    # Verify whether stock-specific queries expose history missing from both
    # the full-market and industry-partition endpoints. Selection uses gaps only.
    missing = pd.read_csv(ROOT/'results/rrg_report_settings_20261107/unclassified_stocks_examples.csv')
    probes = sorted(missing.loc[missing.date.eq('2018-12-28'), 'ts_code'].unique())[:30]
    tasks = [('ci_index_member', {'ts_code': code, 'is_new': current},
              f'membership/probe_{code}_{current}.parquet', 5000)
             for code in probes for current in ['Y', 'N']]
    download.run_tasks(tasks, 4, 'stock_history_probes')
    old_keys = set(map(tuple, members[['ts_code', 'l1_code', 'in_date', 'out_date']].fillna('').to_numpy()))
    frames = [pd.read_parquet(f) for f in sorted((RAW/'membership').glob('*.parquet'))]
    new = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    new = new.drop_duplicates(['ts_code', 'l1_code', 'in_date', 'out_date'])
    new_keys = set(map(tuple, new[['ts_code', 'l1_code', 'in_date', 'out_date']].fillna('').to_numpy()))
    extra = new.loc[[tuple(r) not in old_keys for r in new[['ts_code', 'l1_code', 'in_date', 'out_date']].fillna('').to_numpy()]]
    extra.to_parquet(DATA/'additional_membership_intervals.parquet', index=False)
    download.save_json(DATA/'history_query_audit.json', {
        'old_intervals': len(old_keys), 'refreshed_intervals': len(new_keys),
        'additional_intervals': len(new_keys-old_keys), 'industry_partitions': len(industries),
        'stock_probes': len(probes), 'old_files_preserved': True,
        'scope': 'No backward extension of first observed membership date.'})
    print('Supplemental metadata/history complete; additional intervals:', len(extra), flush=True)


if __name__ == '__main__':
    main()
