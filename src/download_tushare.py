"""Download raw A-share replication inputs; never embeds credentials."""
import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw'
URL = 'https://t.xiaodefa.top/'
PAGE = 6000
lock = threading.Lock()
next_request = 0.0
local = threading.local()


def call(api, params):
    global next_request
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        token = Path(os.environ['TUSHARE_TOKEN_FILE']).read_text().strip()
    if not hasattr(local, 'session'):
        local.session = requests.Session()
    for attempt in range(6):
        with lock:
            delay = max(0, next_request - time.monotonic())
            next_request = max(next_request, time.monotonic()) + float(os.environ.get('TUSHARE_REQUEST_INTERVAL', '0.36'))
        if delay:
            time.sleep(delay)
        try:
            response = local.session.post(URL, json={'api_name': api, 'token': token,
                'params': params, 'fields': ''}, timeout=(15, 90))
            response.raise_for_status()
            payload = response.json()
            if payload.get('code') != 0:
                raise RuntimeError(str(payload.get('msg'))[:300])
            data = payload.get('data')
            if not isinstance(data, dict) or not isinstance(data.get('fields'), list) or not isinstance(data.get('items'), list):
                raise RuntimeError('Malformed response')
            return pd.DataFrame(data['items'], columns=data['fields'])
        except Exception as exc:
            if attempt == 5:
                raise RuntimeError(f'{api}: {str(exc).replace(token, "[REDACTED]")}') from None
            time.sleep(min(60, 2 ** (attempt + 1)))


def download(api, params, relative):
    dest = RAW / relative
    marker = dest.with_suffix('.json')
    if dest.exists() and marker.exists():
        return {'file': str(relative), 'cached': True}
    frames = []
    fingerprints = set()
    offset = 0
    page_size = {'daily_basic': 10000, 'index_member_all': 2000, 'stk_limit': 5800, 'suspend_d': 5000}.get(api, PAGE)
    while api not in ('daily', 'daily_basic', 'adj_factor', 'stk_limit'):
        frame = call(api, {**params, 'limit': page_size, 'offset': offset})
        if not frame.empty:
            fingerprint = hashlib.sha256(frame.to_json().encode()).hexdigest()
            if fingerprint in fingerprints:
                raise RuntimeError(f'{api} ignores pagination at offset {offset}')
            fingerprints.add(fingerprint)
            frames.append(frame)
        if len(frame) < page_size:
            break
        offset += len(frame)
    if api in ('daily', 'daily_basic', 'adj_factor', 'stk_limit'):
        for date in pd.date_range(params['start_date'], params['end_date']):
            date_string = date.strftime('%Y%m%d')
            day_offset = 0
            while True:
                frame = call(api, {'trade_date': date_string, 'limit': page_size, 'offset': day_offset})
                if not frame.empty:
                    if not frame.trade_date.astype(str).eq(date_string).all():
                        raise RuntimeError(f'{api}: wrong date in single-day response')
                    fingerprint = hashlib.sha256(frame.to_json().encode()).hexdigest()
                    if fingerprint in fingerprints:
                        raise RuntimeError(f'{api}: repeated single-day page on {date_string}')
                    fingerprints.add(fingerprint)
                    frames.append(frame)
                if len(frame) < page_size:
                    break
                day_offset += len(frame)
    result = pd.concat(frames, ignore_index=True) if frames else frame
    key = [c for c in ['ts_code', 'trade_date'] if c in result.columns]
    if api == 'suspend_d':
        key += [c for c in ['suspend_type', 'suspend_timing'] if c in result.columns]
    if 'trade_date' in key and result.duplicated(key).any():
        raise RuntimeError(f'{api}: duplicate stock-date records')
    if 'trade_date' in result.columns and not result.empty:
        dates = result['trade_date'].astype(str)
        if 'start_date' in params and (dates.lt(params['start_date']).any() or dates.gt(params['end_date']).any()):
            raise RuntimeError(f'{api}: returned date outside requested range')
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix('.parquet.tmp')
    result.to_parquet(temporary, index=False, compression='zstd')
    temporary.replace(dest)
    report = {'api': api, 'params': params, 'rows': len(result), 'columns': list(result.columns),
        'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
        'sha256': hashlib.sha256(dest.read_bytes()).hexdigest(),
        'source': URL, 'pages': len(frames),
        'retrieval_method': 'single_calendar_days' if api in ('daily', 'daily_basic', 'adj_factor', 'stk_limit') else 'range_pagination'}
    if 'trade_date' in result.columns and not result.empty:
        report.update(first_date=str(result.trade_date.min()), last_date=str(result.trade_date.max()),
            dates=int(result.trade_date.nunique()), stocks=int(result.ts_code.nunique()))
    temporary_marker = marker.with_suffix('.json.tmp')
    temporary_marker.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    temporary_marker.replace(marker)
    return {'file': str(relative), 'rows': len(result)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='19991201')
    parser.add_argument('--end', default='20261031')
    parser.add_argument('--workers', type=int, default=10)
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    tasks = []
    for status in ['L', 'D', 'P']:
        tasks.append(('stock_basic', {'exchange': '', 'list_status': status}, Path(f'metadata/stocks_{status}.parquet')))
    for exchange in ['SSE', 'SZSE', 'BSE']:
        tasks.append(('trade_cal', {'exchange': exchange, 'start_date': args.start, 'end_date': args.end}, Path(f'metadata/calendar_{exchange}.parquet')))
    tasks.append(('namechange', {}, Path('metadata/namechange.parquet')))
    for new in ['Y', 'N']:
        tasks.append(('index_member_all', {'is_new': new}, Path(f'metadata/industry_members_{new}.parquet')))
    for year in range(2000, 2027):
        end_date = f'{year}1231' if year < 2026 else args.end
        tasks.append(('suspend_d', {'start_date': f'{year}0101', 'end_date': end_date}, Path(f'suspend_d/{year}.parquet')))
    for code in ['000001.SH', '399001.SZ', '000300.SH', '000905.SH', '000852.SH', '000985.CSI']:
        tasks.append(('index_daily', {'ts_code': code, 'start_date': args.start, 'end_date': args.end}, Path(f'benchmarks/{code}.parquet')))
    start = datetime.strptime(args.start, '%Y%m%d')
    end = datetime.strptime(args.end, '%Y%m%d')
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first = max(f'{year}{month:02d}01', args.start)
        last = min(f'{year}{month:02d}{calendar.monthrange(year, month)[1]}', args.end)
        for api in ['daily', 'adj_factor', 'daily_basic']:
            tasks.append((api, {'start_date': first, 'end_date': last}, Path(f'{api}/{year}{month:02d}.parquet')))
        if last >= '20070104':
            tasks.append(('stk_limit', {'start_date': max(first, '20070104'), 'end_date': last}, Path(f'stk_limit/{year}{month:02d}.parquet')))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    failures = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download, *task): task for task in tasks}
        for future in as_completed(futures):
            done += 1
            try:
                outcome = future.result()
                print(json.dumps({'done': done, 'total': len(tasks), **outcome}, ensure_ascii=False), flush=True)
            except Exception as exc:
                task = futures[future]
                failure = {'api': task[0], 'params': task[1], 'error': str(exc)}
                failures.append(failure)
                print(json.dumps({'done': done, 'total': len(tasks), 'FAILED': failure}, ensure_ascii=False), flush=True)
    (ROOT / 'data' / 'download_failures.json').write_text(json.dumps(failures, ensure_ascii=False, indent=2))
    print(json.dumps({'complete': not failures, 'tasks': len(tasks), 'failures': len(failures)}), flush=True)
    return bool(failures)

if __name__ == '__main__':
    raise SystemExit(main())
