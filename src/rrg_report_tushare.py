"""Report-period diffusion/ETF replication using available Tushare inputs.

Earlier experiments and vendor code stay unchanged. All assumptions, including
incomplete history and one upstream zero-breadth edge case, are recorded here.
"""
import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from rrg_pipeline import (ROOT, DB as PRIOR_DB, EXCLUDE, bind_upstream,
                          resolve_member_dates, verify_upstream, sql_path)

PARENT = ROOT/'results/rrg_report_settings_20261107'
OUT = PARENT/'diffusion_etf'
DB = OUT/'tushare_report_inputs.duckdb'
START, END, WARMUP = '2018-12-28', '2026-03-31', '2017-01-03'
RAW = ROOT/'data/rrg/raw'


def dump(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        record=json.loads((OUT/'build.json').read_text())
        assert sha(DB)==record['sha256'], 'New input database changed; review before reuse.'
        print('Reusing verified report input database',flush=True)
        return
    bind_upstream()
    from src.store import RRGStore
    temp=DB.with_suffix('.building.duckdb')
    if temp.exists():
        raise RuntimeError('Incomplete new build exists; inspect before retry.')
    with RRGStore(temp,read_only=False):
        pass
    con=duckdb.connect(str(temp));con.execute('SET threads=2')
    con.execute(f'ATTACH {sql_path(PRIOR_DB)} AS previous (READ_ONLY)')
    members=pd.concat([pd.read_parquet(RAW/f'metadata/ci_members_{s}.parquet') for s in ['Y','N']])
    members=members.drop_duplicates(['ts_code','l1_code','in_date','out_date'])
    for col in ['in_date','out_date']:
        members[col]=pd.to_datetime(members[col],format='%Y%m%d')
    members,changes=resolve_member_dates(members)
    con.register('_members',members)
    cal=pd.read_parquet(RAW/'metadata/calendar_SSE.parquet')
    days=pd.DataFrame({'trade_date':pd.to_datetime(cal.loc[cal.is_open.eq(1),'cal_date'],format='%Y%m%d')})
    days=days.loc[days.trade_date.between(WARMUP,END)].sort_values('trade_date')
    con.register('_days',days)
    con.execute('CREATE TABLE trade_days AS SELECT * FROM _days')
    industry=pd.concat([pd.read_parquet(f) for f in sorted((RAW/'industry_daily').glob('*.parquet'))])
    industry['trade_date']=pd.to_datetime(industry.trade_date,format='%Y%m%d')
    industry=industry.loc[industry.trade_date.between(WARMUP,END)].rename(columns={'ts_code':'code'})
    industry['industry_name']=industry.code.map(members.set_index('l1_code').l1_name.to_dict())
    con.register('_industry',industry)
    con.execute('CREATE TABLE zx_industry_daily AS SELECT trade_date,code,industry_name,open,close FROM _industry')
    con.execute('''INSERT INTO zx_membership SELECT DISTINCT d.trade_date,
        m.ts_code,m.l1_code FROM trade_days d JOIN _members m
        ON d.trade_date>=m.in_date AND (m.out_date IS NULL OR d.trade_date<m.out_date)''')
    overlaps=con.execute('''SELECT count(*) FROM (SELECT trade_date,stock_code
        FROM zx_membership GROUP BY ALL HAVING count(*)>1)''').fetchone()[0]
    assert overlaps==0
    con.execute('INSERT INTO zx_membership_coverage VALUES (1,?,?,current_timestamp)',[WARMUP,END])
    con.execute('''CREATE TABLE ashares_close AS SELECT strptime(trade_date,'%Y%m%d') AS date,
        ts_code AS stock_code,close FROM read_parquet(?)
        WHERE trade_date BETWEEN '20170103' AND '20260331'
        AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')''',
        [str(ROOT/'data/raw/daily/*.parquet')])
    con.execute('''CREATE TABLE ashares_float_mv AS SELECT strptime(trade_date,'%Y%m%d') AS date,
        ts_code AS stock_code,close*free_share*10000 AS float_mv
        FROM read_parquet(?) WHERE trade_date BETWEEN '20170103' AND '20260331'
        AND free_share>0 AND close>0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')''',
        [str(ROOT/'data/raw/daily_basic/*.parquet')])
    con.execute('CREATE TABLE etf_meta AS SELECT * FROM previous.etf_meta')
    con.execute('''CREATE TABLE etf_daily AS SELECT strptime(p.trade_date,'%Y%m%d') AS date,
        p.ts_code AS code,p.close*a.adj_factor AS adjclose,
        p.open*a.adj_factor AS adjopen,p.amount*1000 AS amount
        FROM read_parquet(?) p JOIN read_parquet(?) a USING(ts_code,trade_date)
        JOIN etf_meta m USING(ts_code) WHERE p.trade_date BETWEEN '20170103' AND '20260331'
        AND a.adj_factor>0''',[str(RAW/'fund_daily/*.parquet'),str(RAW/'fund_adj/*.parquet')])
    con.execute('''INSERT INTO etf_portfolio SELECT * FROM previous.etf_portfolio
        WHERE end_date<=DATE '2026-03-31' ''')
    codes=con.execute('SELECT ts_code FROM etf_meta').df().ts_code.tolist()
    coverage=pd.MultiIndex.from_product([codes,pd.date_range(WARMUP,END,freq='QE-DEC')],
                                       names=['code','period']).to_frame(index=False)
    con.register('_coverage',coverage)
    con.execute('INSERT INTO etf_portfolio_coverage SELECT code,period,current_timestamp FROM _coverage')
    for table in ['ashares_close','ashares_float_mv','etf_daily']:
        columns=('date','stock_code') if table.startswith('ashares') else ('date','code')
        duplicate=con.execute(f'SELECT count(*) FROM (SELECT {",".join(columns)} FROM {table} GROUP BY ALL HAVING count(*)>1)').fetchone()[0]
        assert duplicate==0,(table,duplicate)
    counts={t:con.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in
            ['trade_days','zx_industry_daily','zx_membership','ashares_close','ashares_float_mv','etf_meta','etf_daily','etf_portfolio']}
    con.close();temp.replace(DB)
    inputs=[PRIOR_DB]+list((RAW/'fund_daily').glob('*.parquet'))+list((RAW/'fund_adj').glob('*.parquet'))
    for name in ['daily','daily_basic']:
        inputs += [f for f in (ROOT/f'data/raw/{name}').glob('*.parquet') if '201701'<=f.stem<='202603']
    dump('input_manifest.json',[{'file':str(f),'sha256':sha(f)} for f in sorted(inputs)])
    dump('build.json',{'sha256':sha(DB),'source_database':str(PRIOR_DB),
                       'source_database_sha256':sha(PRIOR_DB),'counts':counts,
                       'free_float_mv':'daily_basic.close * daily_basic.free_share * 10000 CNY',
                       'member_date_conventions':changes.to_dict('records')})
    print('Built separate report-period Tushare database',counts,flush=True)


def diffusion():
    from src.factor_algo import diffusion_value_weighted,_up_flags
    with duckdb.connect(str(DB),read_only=True) as con:
        price=con.execute('SELECT * FROM ashares_close ORDER BY date,stock_code').df()
        mv=con.execute('SELECT * FROM ashares_float_mv ORDER BY date,stock_code').df()
        membership=con.execute('SELECT * FROM zx_membership ORDER BY trade_date,stock_code').df()
    close=price.pivot(index='date',columns='stock_code',values='close')
    mem=membership.pivot(index='trade_date',columns='stock_code',values='industry_code').reindex(index=close.index,columns=close.columns)
    cap=mv.pivot(index='date',columns='stock_code',values='float_mv').reindex(index=close.index,columns=close.columns)
    print('Calculating free-float breadth on',close.shape,flush=True)
    # Reuse original arithmetic. Its all-valid-stocks-down case returns NaN
    # because sum(min_count=1) sees no winners; mathematically breadth is zero.
    # Correct only that identified case before the report's MA20, outside vendor.
    original_daily=diffusion_value_weighted(close,mem,cap,lookback=220,smooth_window=1)
    corrected=original_daily.copy()
    flags=_up_flags(close,220)
    fixed=[]
    for ind in corrected.columns:
        valid=mem.eq(ind)&flags.notna()&cap.gt(0)
        no_winners=(valid&flags.eq(1)).sum(axis=1).eq(0)
        zero=valid.any(axis=1)&no_winners
        assert original_daily.loc[zero,ind].isna().all()
        corrected.loc[zero,ind]=0.
        fixed.extend({'date':d,'industry':ind} for d in corrected.index[zero])
    # Verify the edge case on a declining synthetic industry using upstream code.
    sample=pd.DataFrame({'A':[10.,9.,8.],'B':[10.,8.,7.]})
    labels=pd.DataFrame('I',index=sample.index,columns=sample.columns)
    caps=pd.DataFrame(1.,index=sample.index,columns=sample.columns)
    assert diffusion_value_weighted(sample,labels,caps,lookback=1,smooth_window=1).iloc[1:].isna().all().all()
    cols=sorted(set(corrected.columns)-set(EXCLUDE))
    breadth=corrected[cols].rolling(20).mean()
    legacy=original_daily[cols].rolling(20).mean()
    breadth.to_parquet(OUT/'free_float_diffusion.parquet')
    legacy.to_parquet(OUT/'upstream_zero_missing_diffusion.parquet')
    pd.DataFrame(fixed,columns=['date','industry']).to_csv(OUT/'all_down_zero_corrections.csv',index=False)
    dump('diffusion_implementation.json',{
        'vendor_unchanged':True,'lookback':220,'smoothing':20,
        'stock_price':'Unadjusted close; report adjustment treatment unspecified; upstream convention retained.',
        'missing_industry':'Excluded; current classification never backfilled into earlier dates.',
        'missing_price_or_free_float':'Excluded from the daily valid denominator by upstream logic.',
        'zero_breadth_patch':'Valid denominator and zero winners => 0 before MA20; upstream NaN retained as separate comparison.',
        'corrected_industry_days':len(fixed),'synthetic_edge_case_verified':True})
    print('Breadth complete; all-down industry-day corrections:',len(fixed),flush=True)
    return breadth,legacy


def validate(pf,w,close,fee):
    nav,cash,orders=pf.value(),pf.cash(),pf.orders.records_readable
    assert np.isfinite(nav).all() and nav.gt(0).all() and cash.min()>=-1e-5
    np.testing.assert_allclose(orders.Fees,orders.Size*orders.Price*fee,atol=1e-6)
    signed=orders.Size.where(orders.Side.eq('Buy'),-orders.Size)
    shares=signed.groupby(orders.Column).cumsum()
    assert shares.min()>=-1e-5
    terminal=signed.groupby(orders.Column).sum()
    marks=close.ffill().iloc[-1].reindex(terminal.index)
    assert marks.notna().all()
    value=float(cash.iloc[-1]+(terminal*marks).sum())
    np.testing.assert_allclose(value,nav.iloc[-1],atol=1e-4)
    np.testing.assert_allclose(nav.iloc[-1]/1e7-1,pf.total_return(),rtol=1e-10)
    offsets=nav.index.get_indexer(w.index)+1
    execution=nav.index[offsets[offsets<len(nav.index)]]
    assert orders.Timestamp.isin(execution).all()
    return {'accounting_passed':True,'fees_passed':True,'next_open_dates_passed':True,
            'ending_value_cny':value,'profit_cny':value-1e7,
            'fees_cny':float(orders.Fees.sum()),'first_trade':str(orders.Timestamp.min())}


def run():
    bind_upstream(DB)
    from src.data_provider import fetch_etf_meta,fetch_etf_daily
    from src.strategy import select_by_diffusion,select_diffusion_with_rrg
    from src.exposure import ETFExposureBuilder
    from src.store import RRGStore
    from src.backtest import (build_overseas_share,build_report_universe_panel,
        build_exposure_panel,mask_listed,run_vbt_backtest,portfolio_stats)
    from src.optimizer import batch_optimize
    from src.utils import month_end_dates,to_equal_weight
    import vectorbt as vbt
    ratio=pd.read_parquet(PARENT/'rs_ratio.parquet');mom=pd.read_parquet(PARENT/'rs_momentum.parquet')
    breadth,legacy=diffusion()
    breadth=breadth.reindex(index=ratio.index,columns=ratio.columns)
    legacy=legacy.reindex(index=ratio.index,columns=ratio.columns)
    combined=select_diffusion_with_rrg(breadth,ratio,mom,top_n=6,keep_quadrants=(1,2))
    pure=select_by_diffusion(breadth,top_n=6)
    legacy_combined=select_diffusion_with_rrg(legacy,ratio,mom,top_n=6,keep_quadrants=(1,2))
    me=month_end_dates(ratio.index);me=me[(me>=START)&(me<=END)]
    scores=combined.loc[me].astype(float)
    scores.to_parquet(OUT/'industry_selection.parquet')
    combined.to_parquet(OUT/'daily_combined_signals.parquet')
    pure.loc[me].to_parquet(OUT/'pure_diffusion_signals.parquet')
    signal_changed=int((combined.loc[me]!=legacy_combined.loc[me]).any(axis=1).sum())
    invalid=breadth.loc[me].isna()
    invalid.to_csv(OUT/'missing_diffusion_at_formation.csv')
    # Prefix invariance checks that future prices cannot alter earlier selections.
    prefix=me[me<='2022-12-30']
    pd.testing.assert_frame_equal(combined.loc[prefix],
        select_diffusion_with_rrg(breadth.loc[:prefix[-1]],ratio.loc[:prefix[-1]],mom.loc[:prefix[-1]],
                                  top_n=6,keep_quadrants=(1,2)).loc[prefix])
    meta=fetch_etf_meta(only_listed=True,drop_qdii=True)
    meta=meta.loc[meta.list_date.le(pd.Timestamp(END))]
    codes=sorted(meta.index)
    close,open_,amount=fetch_etf_daily(codes,START,END,lookback=25)
    with RRGStore(DB) as store:
        with ETFExposureBuilder(codes,START,END,store=store,classification='zx') as builder:
            iw=builder.build_industry_weights()
            overseas=build_overseas_share(builder.load_portfolio())
    iw.to_parquet(OUT/'visible_etf_industry_weights.parquet')
    panel=build_report_universe_panel(meta,amount,me,lookback=20,
        overseas_share=overseas,overseas_threshold=.5)
    exposures=build_exposure_panel(panel,iw)
    dates=pd.DatetimeIndex([d for d in me if d in exposures])
    assert dates.equals(me),'Missing ETF exposure date; review instead of dropping dates.'
    panel.to_parquet(OUT/'candidate_etf_panel.parquet')
    weights={}
    for name,preset in [('Report_base','base'),('Report_cap20_cash','etf_capped'),
                        ('Report_no_industry_cap','unconstrained')]:
        print('Optimizing',name,len(dates),'dates',flush=True)
        weights[name]=mask_listed(batch_optimize(scores,exposures=exposures,dates=dates,
                                                preset=preset,strict=True),meta)
    # Keep the existing two baseline definitions fixed for a comparable extension.
    industry_close=pd.read_parquet(PARENT/'industry_close.parquet')
    with duckdb.connect(str(DB),read_only=True) as con:
        hist=con.execute('SELECT trade_date,code,close FROM zx_industry_daily ORDER BY 1,2').df()
    industry_close=hist.pivot(index='trade_date',columns='code',values='close').drop(columns=EXCLUDE)
    momentum=select_by_diffusion(industry_close.pct_change(220,fill_method=None),top_n=6).loc[dates].astype(float)
    weights['Industry_momentum_220_6']=mask_listed(batch_optimize(momentum,exposures=exposures,
                                                dates=dates,preset='base',strict=True),meta)
    weights['ETF_equal_weight']=pd.DataFrame({d:pd.Series(1/len(exposures[d]),index=exposures[d].index)
                                             for d in dates}).T
    if signal_changed:
        print('Running original zero-as-missing implementation for comparison',flush=True)
        weights['Base_upstream_zero_missing']=mask_listed(batch_optimize(legacy_combined.loc[dates].astype(float),
            exposures=exposures,dates=dates,preset='base',strict=True),meta)
    cols=sorted(set().union(*(set(w.columns) for w in weights.values())))
    assert set(cols).issubset(close.columns)
    close,open_=close[cols].loc[START:],open_[cols].loc[START:]
    vbt.settings.portfolio['call_seq']='auto'
    rows,checks,navs=[],{},{}
    published={'Report_base':(.2151,-.3498),'Report_cap20_cash':(.1851,-.2909),
               'Report_no_industry_cap':(.3153,-.3333)}
    for name,w in weights.items():
        w=w.reindex(index=dates,columns=cols).fillna(0.)
        assert w.min().min()>=-1e-8 and w.sum(axis=1).max()<=1+1e-5
        for d,E in exposures.items():
            if d not in dates:continue
            score=scores.loc[d] if name!='Industry_momentum_220_6' else momentum.loc[d]
            k=int(score.sum())
            if name in ['Report_base','Report_cap20_cash','Industry_momentum_220_6'] and k:
                selected=score[score>0].index
                exp=E.reindex(columns=selected,fill_value=0).T@w.loc[d].reindex(E.index)
                assert exp.max()<=1/k+2e-5,(name,str(d),float(exp.max()),k)
        if name=='Report_cap20_cash':assert w.max().max()<=.2+1e-5
        w.to_parquet(OUT/f'{name}_weights.parquet')
        pf=run_vbt_backtest(w,close,open_,fees=.0003,slippage=0.,init_cash=1e7)
        check=validate(pf,w,close,.0003)
        nav=pf.value();orders=pf.orders.records_readable
        nav.to_csv(OUT/f'{name}_nav.csv');orders.to_csv(OUT/f'{name}_orders.csv',index=False)
        metric=dict(portfolio_stats(pf),strategy=name,**check)
        if name in published:
            metric.update(report_cagr=published[name][0],report_drawdown=published[name][1])
        rows.append(metric);checks[name]=check;navs[name]=nav/1e7
        print(name,'CAGR',metric['年化收益率'],'DD',metric['最大回撤'],flush=True)
    # Paper sections 2.2/2.3 use industry indices, kept separate from ETF portfolios.
    sector=pd.read_parquet(PARENT/'industry_close.parquet')
    with duckdb.connect(str(DB),read_only=True) as con:
        sector_open=con.execute('SELECT trade_date,code,open FROM zx_industry_daily ORDER BY 1,2').df()
    sector_open=sector_open.pivot(index='trade_date',columns='code',values='open').reindex(index=sector.index,columns=sector.columns)
    for name,sig in [('Industry_diffusion',pure),('Industry_diffusion_rrg',combined)]:
        w=to_equal_weight(sig.loc[dates]).fillna(0.)
        pf=run_vbt_backtest(w,sector,sector_open,fees=.0003,slippage=0.,init_cash=1e7)
        check=validate(pf,w,sector,.0003)
        pf.value().to_csv(OUT/f'{name}_nav.csv')
        pf.orders.records_readable.to_csv(OUT/f'{name}_orders.csv',index=False)
        rows.append(dict(portfolio_stats(pf),strategy=name,**check));checks[name]=check
    metrics=pd.DataFrame(rows).set_index('strategy');metrics.to_csv(OUT/'performance.csv')
    pd.DataFrame(navs).to_csv(OUT/'etf_net_nav.csv')
    annual={name:(1+nav.pct_change().fillna(0)).groupby(nav.index.year).prod()-1 for name,nav in navs.items()}
    pd.DataFrame(annual).to_csv(OUT/'annual_returns.csv')
    exposure_coverage=pd.DataFrame({d:E.sum(axis=1).describe() for d,E in exposures.items()}).T
    exposure_coverage.to_csv(OUT/'visible_industry_coverage.csv')
    dump('validation.json',checks)
    dump('run.json',{'status':'completed_available_data_replication',
        'user_approved':'Continue with Tushare available data and retain explicit report deviations.',
        'period':[START,END],'warmup':WARMUP,'upstream_commit':verify_upstream(),
        'input_database':str(DB),'input_sha256':sha(DB),'params':[220,60,20,6],
        'fees':.0003,'slippage':0,'initial_cash':1e7,'execution':'month-end target; next-session open; sell first; explicit zero exits',
        'weighting':'free_share * unadjusted close, not circ_mv',
        'zero_edge_case_changed_formation_dates':signal_changed,
        'monthly_diffusion_missing_cells':int(invalid.sum().sum()),
        'deviations':[
            '2018/2019 historical industry membership coverage approximately 56%/60%; missing classifications excluded.',
            'Only disclosed fund holdings; no normalization of unknown industry exposure.',
            'Current-listed ETF proxy universe, non-QDII with overseas filtering; not strict historical industry/theme universe.',
            'Latest-announcement dedup inherited from stored data; report exact disclosure-vintage rule unspecified.',
            'Raw stock close convention inherited; report stock-price adjustment rule unspecified.',
            'All valid constituents down: mathematical zero corrected outside vendor and original implementation compared.',
            'Same free_share label does not establish identical Wind free-float definition.',
            'Fee 3bp and 252-day annualization; execution timing/annualization details not fully specified in report.']})
    lines=['研报区间：Tushare可获取数据下的复现','',f'区间：{START} 至 {END}。',
           '保留此前冻结实验；本次使用自行下载的Tushare兼容接口数据，采用自由流通市值加权。',
           '核心参数220/60/20，扩散前6再保留RRG一二象限，月末形成组合、次日开盘、单边万三。',
           '复用原开源优化器及回测器，统一显式退出、先卖后买。','',metrics.to_string(),'',
           '这是保留数据差异的方法复现，不是原研报商业数据库数值的严格复刻。',
           '2018/2019行业归属覆盖约56%/60%，未分类股票剔除，不按今天的行业向前回填。',
           '季度ETF持仓部分仅前十大；未知行业暴露不归一化成已知暴露。',
           'ETF池沿用当前上市非QDII代理池及境外过滤，存在生存偏差和候选标签差异。',
           '自由流通股本用free_share计算，和Wind定义的一致性尚未核实。',
           '原扩散函数把“全体有效成分均未上涨”算为NaN，本次按公式纠正为0后MA20，vendor不改。',
           f'纠正影响月末选择的日期数：{signal_changed}；如有影响，原实现基础组合单独保存。',
           '原文行业指数层费用未明确，本次行业指数对照按3bp处理；它们是假设行业指数组合。',
           '研报表13月频年化21.51%、表16月频21.01%，对照主线采用表13。',
           '当前年化与研报的差距同时涉及数据、持仓覆盖、候选池与执行约定，不能只归因某一项。',
           '沪深300增强和中信二级行业不在本轮主线范围，需要相应历史权重/分类输入。',
           '所有保存净值、费用、期末资产和次日执行日期已核对。']
    (OUT/'RESULTS.txt').write_text('\n'.join(lines))
    print('Completed:',OUT,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',choices=['build','run','all'],default='all')
    args=parser.parse_args()
    if args.stage in ['build','all']:build()
    if args.stage in ['run','all']:run()
