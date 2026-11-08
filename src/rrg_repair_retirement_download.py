"""Query missing retirement-period prices and NAVs from the same gateway."""
from datetime import datetime, timezone
import hashlib
import json

import pandas as pd

import download_rrg as download
from rrg_repair_download import DATA, RAW
from rrg_repair_inputs import OUT


def main():
    download.DATA, download.RAW = DATA, RAW
    meta=pd.read_parquet(OUT/'etf_metadata_audit.parquet')
    effective=pd.read_csv(OUT/'effective_candidate_history.csv')
    quotes=pd.read_csv(OUT/'etf_quote_coverage.csv')
    audit=meta.loc[meta.list_status.eq('D') & meta.ts_code.isin(effective.code)
                   & meta.delist_date.le(pd.Timestamp('2026-03-31'))].merge(
                       quotes[['ts_code','last_quote']],on='ts_code').rename(columns={'ts_code':'code'})
    audit=audit.loc[audit.last_quote.notna() & audit.last_quote.lt('2026-03-31')]
    tasks=[]
    for row in audit.itertuples():
        code=row.code
        start=(pd.Timestamp(row.last_quote)-pd.Timedelta(days=15)).strftime('%Y%m%d')
        for api,page in [('fund_daily',5000),('fund_adj',2000)]:
            tasks.append((api,{'ts_code':code,'start_date':start,'end_date':'20260331'},f'{api}/{code}.parquet',page))
        for alias in [code,code[:6]+'.OF']:
            tasks.append(('fund_nav',{'ts_code':alias,'start_date':start,'end_date':'20260331'},f'fund_nav/{alias}.parquet',2000))
    try:
        download.run_tasks(tasks,4,'retirement_quotes_and_nav')
    except RuntimeError:
        failures=json.loads((DATA/'download_status.json').read_text())['failures']
        for failed in failures:
            assert failed['api']=='fund_nav' and 'repeated page' in failed['error']
            params=failed['params']; frames=[]
            # One gateway query ignores offset. Check every month, then query
            # each calendar day in non-empty months, so rows are never truncated.
            for month in pd.period_range(params['start_date'],params['end_date'],freq='M'):
                low=max(params['start_date'],month.start_time.strftime('%Y%m%d'))
                high=min(params['end_date'],month.end_time.strftime('%Y%m%d'))
                part=download.call('fund_nav',{**params,'start_date':low,'end_date':high,'limit':2000})
                if part.empty: continue
                for day in pd.date_range(low,high):
                    day=day.strftime('%Y%m%d')
                    one=download.call('fund_nav',{'ts_code':params['ts_code'],'nav_date':day,'limit':2000})
                    if one.empty: continue
                    assert len(one)<2000 and one.nav_date.eq(day).all()
                    frames.append(one)
            result=pd.concat(frames,ignore_index=True).drop_duplicates()
            dest=RAW/failed['file'];result.to_parquet(dest,index=False,compression='zstd')
            download.save_json(dest.with_suffix('.json'),{
                'api':'fund_nav','params':params,'rows':len(result),'file':failed['file'],
                'source':download.URL,'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),
                'sha256':hashlib.sha256(dest.read_bytes()).hexdigest(),
                'pagination_repair':'Every month checked; nonempty months queried by each nav_date.'})
        download.save_json(DATA/'retirement_download_repair.json',{'resolved_failures':len(failures)})
    rows=[]
    for row in audit.itertuples():
        code=row.code
        price=pd.read_parquet(RAW/f'fund_daily/{code}.parquet')
        nav=pd.concat([pd.read_parquet(RAW/f'fund_nav/{c}.parquet') for c in [code,code[:6]+'.OF']])
        rows.append({'code':code,'previous_last_quote':row.last_quote,
            'refreshed_last_quote':price.trade_date.max() if not price.empty else None,
            'additional_price_dates':int(price.trade_date.gt(row.last_quote.replace('-','')).sum()) if not price.empty else 0,
            'nav_rows':len(nav),'last_nav_date':nav.nav_date.max() if not nav.empty else None,
            'last_nav_ann_date':nav.ann_date.max() if not nav.empty else None})
    pd.DataFrame(rows).to_csv(OUT/'retirement_data_query_audit.csv',index=False)
    print('Retirement queries complete.',flush=True)


if __name__=='__main__':
    main()
