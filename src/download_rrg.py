"""Collect raw RRG prerequisites; credentials stay outside the project."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
from download_tushare import call, URL

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/rrg'
RAW = DATA / 'raw'


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def validate_frame(frame, api, params):
    if frame.empty:
        return
    if 'ts_code' in params and 'ts_code' in frame:
        if not frame.ts_code.eq(params['ts_code']).all():
            raise ValueError(f'{api}: wrong code returned')
    if api == 'etf_basic' and 'list_status' in frame:
        if not frame.list_status.eq(params['list_status']).all():
            raise ValueError(f'{api}: listing-status filter ignored')
    if api == 'ci_index_member' and 'is_new' in frame:
        if not frame.is_new.eq(params['is_new']).all():
            raise ValueError(f'{api}: historical-membership filter ignored')
    if 'trade_date' in frame:
        dates = pd.to_datetime(frame.trade_date.astype(str), format='%Y%m%d', errors='raise')
        if 'start_date' in params and dates.lt(pd.Timestamp(params['start_date'])).any():
            raise ValueError(f'{api}: date earlier than requested')
        if 'end_date' in params and dates.gt(pd.Timestamp(params['end_date'])).any():
            raise ValueError(f'{api}: date later than requested')
        if 'ts_code' in frame and frame.duplicated(['ts_code', 'trade_date']).any():
            raise ValueError(f'{api}: duplicate code/date key')
    if api == 'fund_portfolio':
        required = {'ts_code', 'ann_date', 'end_date', 'symbol'}
        if not required.issubset(frame):
            raise ValueError(f'{api}: point-in-time fields absent')
        for column in ['ann_date', 'end_date']:
            pd.to_datetime(frame[column].astype(str), format='%Y%m%d', errors='raise')
        if frame.duplicated(['ts_code', 'ann_date', 'end_date', 'symbol']).any():
            raise ValueError(f'{api}: duplicate disclosure key')


def fetch(api, params, relative, page_size):
    dest = RAW / relative
    marker = dest.with_suffix('.json')
    if dest.exists() and marker.exists():
        old = json.loads(marker.read_text())
        if (old['api'] == api and old['params'] == params
                and hashlib.sha256(dest.read_bytes()).hexdigest() == old['sha256']):
            return {**old, 'cached': True}
        raise ValueError(f'Cache identity/checksum mismatch: {relative}')
    frames, seen, offset = [], set(), 0
    while True:
        frame = call(api, {**params, 'limit': page_size, 'offset': offset})
        if frame.empty:
            break
        # Short pages may reflect a relay-specific ceiling. Stop only on empty.
        digest = hashlib.sha256(frame.to_json().encode()).hexdigest()
        if digest in seen:
            raise ValueError(f'{api}: repeated page at offset {offset}')
        seen.add(digest)
        frames.append(frame)
        offset += len(frame)
    result = pd.concat(frames, ignore_index=True) if frames else frame
    validate_frame(result, api, params)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix('.parquet.tmp')
    result.to_parquet(temporary, index=False, compression='zstd')
    temporary.replace(dest)
    summary = {'api': api, 'params': params, 'rows': len(result), 'columns': list(result),
        'file': str(relative), 'pages': len(frames), 'source': URL,
        'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(dest.read_bytes()).hexdigest()}
    save_json(marker, summary)
    return summary


def run_tasks(tasks, workers, phase):
    failures, done = [], 0
    def update():
        save_json(DATA / 'download_status.json', {'phase': phase, 'done': done,
            'total': len(tasks), 'failures': failures,
            'updated_at_utc': datetime.now(timezone.utc).isoformat()})
    update()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(fetch, *task): task for task in tasks}
        for future in as_completed(pending):
            done += 1
            try:
                summary = future.result()
                print(json.dumps({'phase': phase, 'done': done, 'total': len(tasks),
                    'file': summary['file'], 'rows': summary['rows']}, ensure_ascii=False), flush=True)
            except Exception as exc:
                task = pending[future]
                failures.append({'api': task[0], 'params': task[1], 'file': task[2],
                    'error': str(exc)[:300]})
                print(json.dumps({'phase': phase, 'failed_file': task[2],
                    'error': str(exc)[:300]}, ensure_ascii=False), flush=True)
            update()
    if failures:
        raise RuntimeError(f'{phase}: {len(failures)} failed tasks; see download_status.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='20170101')
    parser.add_argument('--end', default='20261030')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    if not os.environ.get('TUSHARE_TOKEN') and not os.environ.get('TUSHARE_TOKEN_FILE'):
        parser.error('Set TUSHARE_TOKEN or TUSHARE_TOKEN_FILE')
    DATA.mkdir(parents=True, exist_ok=True)
    fetch('trade_cal', {'exchange': 'SSE', 'start_date': args.start, 'end_date': args.end},
        'metadata/calendar_SSE.parquet', 6000)
    metadata = []
    for status in ['L', 'D', 'P']:
        metadata.append(('etf_basic', {'list_status': status}, f'metadata/etf_{status}.parquet', 5000))
    for status in ['L', 'D', 'I']:
        metadata.append(('fund_basic', {'market': 'E', 'status': status}, f'metadata/listed_funds_{status}.parquet', 5000))
    for current in ['Y', 'N']:
        metadata.append(('ci_index_member', {'is_new': current}, f'metadata/ci_members_{current}.parquet', 5000))
    run_tasks(metadata, args.workers, 'metadata')
    etfs = pd.concat([pd.read_parquet(RAW / f'metadata/etf_{s}.parquet') for s in ['L', 'D', 'P']], ignore_index=True)
    if etfs.empty or 'ts_code' not in etfs or etfs.ts_code.duplicated().any():
        raise ValueError('ETF metadata empty or statuses overlap; inspect before continuing')
    members = pd.concat([pd.read_parquet(RAW / f'metadata/ci_members_{s}.parquet') for s in ['Y', 'N']], ignore_index=True)
    if members.empty or 'l1_code' not in members:
        raise ValueError('CITIC membership empty; inspect before continuing')
    tasks = []
    for code in sorted(members.l1_code.dropna().unique()):
        tasks.append(('ci_daily', {'ts_code': code, 'start_date': args.start, 'end_date': args.end}, f'industry_daily/{code}.parquet', 4000))
    for month in pd.period_range(pd.Timestamp(args.start), pd.Timestamp(args.end), freq='M'):
        low = max(args.start, month.start_time.strftime('%Y%m%d'))
        high = min(args.end, month.end_time.strftime('%Y%m%d'))
        for api, limit in [('fund_daily', 5000), ('fund_adj', 2000)]:
            tasks.append((api, {'start_date': low, 'end_date': high}, f'{api}/{month.strftime("%Y%m")}.parquet', limit))
    # All ETF types collected first; no strategy selection or survivorship filter.
    for row in etfs.itertuples():
        listing = getattr(row, 'list_date', None)
        if listing is not None and pd.notna(listing) and str(listing) > args.end:
            continue
        tasks.append(('fund_portfolio', {'ts_code': row.ts_code, 'start_date': args.start,
            'end_date': args.end}, f'fund_portfolio/{row.ts_code}.parquet', 2000))
    save_json(DATA / 'download_plan.json', {'start': args.start, 'end': args.end,
        'industry': 'CITIC', 'tasks': [{'api': a, 'params': p, 'file': f, 'page_size': n} for a, p, f, n in tasks],
        'reused_equity_data': '../clean/daily and ../clean/reference',
        'note': 'Raw collection; historical completeness requires audit.'})
    run_tasks(tasks, args.workers, 'raw_inputs')
    save_json(DATA / 'download_status.json', {'phase': 'raw_download_complete_pending_audit',
        'tasks': len(tasks), 'start': args.start, 'end': args.end})


if __name__ == '__main__':
    main()
