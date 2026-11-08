"""Run report-period industry RRG and audit prerequisites for ETF replication.

Uses existing Tushare-compatible raw files and unmodified upstream functions.
Writes only to a new report_settings directory; never rewrites earlier studies.
"""
from pathlib import Path
import hashlib
import json

import duckdb
import numpy as np
import pandas as pd

from rrg_pipeline import ROOT, EXCLUDE, verify_upstream, resolve_member_dates

TARGET = ROOT / 'results/rrg_report_settings_20261107'
START, END, WARMUP = '2018-12-28', '2026-03-31', '2017-01-03'


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit():
    raw = ROOT / 'data/rrg/raw'
    members = pd.concat([pd.read_parquet(raw / f'metadata/ci_members_{s}.parquet')
                         for s in ['Y', 'N']], ignore_index=True)
    members = members.drop_duplicates(['ts_code', 'l1_code', 'in_date', 'out_date'])
    for col in ['in_date', 'out_date']:
        members[col] = pd.to_datetime(members[col], format='%Y%m%d')
    members, changes = resolve_member_dates(members)
    changes.to_csv(TARGET / 'membership_date_conventions.csv', index=False)
    con = duckdb.connect()
    con.execute('SET threads=2')
    con.register('members', members)
    # Audit only the requested history; all read_parquet inputs remain read-only.
    con.execute('''CREATE TEMP TABLE equity AS SELECT ts_code,
        strptime(trade_date,'%Y%m%d') AS date, close, free_share
        FROM read_parquet(?) WHERE trade_date BETWEEN '20170103' AND '20260331'
        AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')''',
        [str(ROOT / 'data/raw/daily_basic/*.parquet')])
    con.execute('''CREATE TEMP TABLE coverage AS
        SELECT a.ts_code, a.date, a.close, a.free_share,
        count(m.l1_code) AS assignments FROM equity a LEFT JOIN members m
        ON a.ts_code=m.ts_code AND a.date>=m.in_date
        AND (m.out_date IS NULL OR a.date<m.out_date) GROUP BY ALL''')
    annual = con.execute('''SELECT year(date) AS year, count(*) AS observed_stock_days,
        count(*) FILTER(WHERE assignments=1) AS classified_stock_days,
        count(*) FILTER(WHERE assignments>1) AS ambiguous_stock_days,
        count(*) FILTER(WHERE free_share>0 AND close>0) AS usable_free_float_days
        FROM coverage GROUP BY 1 ORDER BY 1''').df()
    annual['classified_fraction'] = annual.classified_stock_days / annual.observed_stock_days
    annual['free_float_fraction'] = annual.usable_free_float_days / annual.observed_stock_days
    annual.to_csv(TARGET / 'historical_data_coverage.csv', index=False)
    daily = con.execute('''SELECT date,count(*) AS observed_stocks,
        count(*) FILTER(WHERE assignments=1) AS classified_stocks
        FROM coverage GROUP BY 1 ORDER BY 1''').df()
    daily['classified_fraction'] = daily.classified_stocks/daily.observed_stocks
    daily.to_parquet(TARGET / 'daily_membership_coverage.parquet', index=False)
    sample = con.execute('''SELECT ts_code,date FROM coverage WHERE assignments=0
        AND date IN (DATE '2018-12-28', DATE '2019-12-30')
        ORDER BY date,ts_code''').df()
    sample.to_csv(TARGET / 'unclassified_stocks_examples.csv', index=False)
    con.close()
    industry_files = sorted((raw / 'industry_daily').glob('*.parquet'))
    industry = pd.concat([pd.read_parquet(f) for f in industry_files], ignore_index=True)
    industry['date'] = pd.to_datetime(industry.trade_date, format='%Y%m%d')
    industry = industry.loc[industry.date.between(WARMUP, END)]
    calendar = pd.read_parquet(raw/'metadata/calendar_SSE.parquet')
    days = pd.DatetimeIndex(pd.to_datetime(calendar.loc[calendar.is_open.eq(1), 'cal_date'], format='%Y%m%d'))
    days = days[(days>=WARMUP)&(days<=END)].sort_values()
    price = industry.pivot(index='date', columns='ts_code', values='close').reindex(days)
    open_ = industry.pivot(index='date', columns='ts_code', values='open').reindex(days)
    cols = sorted(set(price.columns)-set(EXCLUDE))
    price, open_ = price[cols], open_[cols]
    if len(cols)!=28 or price.isna().any().any() or open_.isna().any().any():
        raise RuntimeError('Industry close/open calendar incomplete; inspect source instead of filling.')
    if not (price>0).all().all() or not (open_>0).all().all():
        raise RuntimeError('Non-positive industry price')
    # The report requires complete historical classification and all ETF holdings.
    # A coverage audit is evidence, not a reason to invent classifications.
    blockers = [
        '早期中信历史行业归属覆盖不足，尚无法按研报计算完整的自由流通市值加权扩散指标。',
        'ETF行业/主题候选标签及退市历史需核对；原作者全非QDII代理池不是研报的严格候选池。',
        'fund_portfolio季度披露存在前十大持仓，尚未满足研报“全部持仓”的ETF行业暴露要求。',
    ]
    facts = {'annual_coverage': annual.to_dict('records'),
             'free_float_formula': 'unadjusted daily_basic.close * free_share * 10000 (CNY)',
             'free_float_definition_match_to_Wind': 'not verified',
             'industry_days': len(days), 'industry_count': len(cols),
             'industry_open_close_complete': True,
             'strict_diffusion_etf_status': 'blocked_by_input_coverage', 'blockers': blockers}
    write_json(TARGET/'input_audit.json', facts)
    inputs = industry_files + [raw/f'metadata/ci_members_{s}.parquet' for s in ['Y','N']]
    inputs += [raw/'metadata/calendar_SSE.parquet']
    inputs += [f for f in sorted((ROOT/'data/raw/daily_basic').glob('*.parquet'))
               if '201701'<=f.stem<='202603']
    write_json(TARGET/'input_manifest.json', [{'file': str(f), 'sha256': digest(f)} for f in inputs])
    return price, open_, facts


def main():
    TARGET.mkdir(exist_ok=False)
    commit = verify_upstream()
    import sys
    sys.path.insert(0, str(ROOT/'vendor/quantsplaybook_rrg'))
    from src.factor_algo import equal_weight_benchmark, compute_rrg
    from src.strategy import select_by_quadrant
    from src.utils import month_end_dates, to_equal_weight
    from src.backtest import run_vbt_backtest, portfolio_stats
    import vectorbt as vbt
    price, open_, facts = audit()
    benchmark = equal_weight_benchmark(price)
    ratio, mom = compute_rrg(price, benchmark, 220, 60, 20)
    cutoff = pd.Timestamp('2022-12-30')
    prefix_price = price.loc[:cutoff]
    prefix_benchmark = equal_weight_benchmark(prefix_price)
    prefix_ratio, prefix_mom = compute_rrg(prefix_price, prefix_benchmark, 220, 60, 20)
    pd.testing.assert_frame_equal(ratio.loc[:cutoff], prefix_ratio)
    pd.testing.assert_frame_equal(mom.loc[:cutoff], prefix_mom)
    dates = month_end_dates(price.index)
    dates = dates[(dates>=START)&(dates<=END)]
    assert ratio.loc[dates].notna().all().all() and mom.loc[dates].notna().all().all()
    price.loc[START:].to_parquet(TARGET/'industry_close.parquet')
    ratio.to_parquet(TARGET/'rs_ratio.parquet')
    mom.to_parquet(TARGET/'rs_momentum.parquet')
    benchmark.loc[START:].to_csv(TARGET/'industry_equal_weight_daily_benchmark.csv')
    # The report does not specify execution time or industry-layer transaction fees.
    # Preserve monthly formation + next-open execution and show 0/3bp separately.
    vbt.settings.portfolio['call_seq'] = 'auto'
    groups = [(1,), (2,), (3,), (4,), (1,2), (1,4), (3,4), (2,3)]
    published = {'Q1': (.1834,-.3667), 'Q2': (.0581,-.4563),
                 'Q3': (.075,-.2894), 'Q4': (.0808,-.3586),
                 'Q1_2': (.1874,-.3282), 'Q1_4': (.1611,-.4186),
                 'Q3_4': (.0837,-.3126), 'Q2_3': (.0523,-.4559)}
    rows, checks, navs = [], {}, {}
    for qs in groups:
        name = 'Q'+'_'.join(map(str,qs))
        sig = select_by_quadrant(ratio,mom,quadrants=qs,top_n=6).loc[dates]
        w = to_equal_weight(sig).fillna(0.)
        assert sig.sum(axis=1).le(6).all()
        assert np.allclose(w.sum(axis=1),sig.any(axis=1).astype(float))
        w.to_parquet(TARGET/f'{name}_weights.parquet')
        for bp in [0,3]:
            key = f'{name}_{bp}bp'
            pf = run_vbt_backtest(w, price.loc[START:], open_.loc[START:],
                                  fees=bp/10000, slippage=0., init_cash=1e7)
            nav, orders, cash = pf.value(),pf.orders.records_readable,pf.cash()
            np.testing.assert_allclose(orders.Fees,orders.Size*orders.Price*bp/10000,atol=1e-6)
            assert cash.min()>=-1e-5
            offset=nav.index.get_indexer(w.index)+1
            execution=nav.index[offset[offset<len(nav.index)]]
            assert orders.Timestamp.isin(execution).all()
            signed=orders.Size.where(orders.Side.eq('Buy'),-orders.Size)
            terminal=signed.groupby(orders.Column).sum()
            value=float(cash.iloc[-1]+(terminal*price.iloc[-1].reindex(terminal.index)).sum())
            np.testing.assert_allclose(value,nav.iloc[-1],atol=1e-5)
            profit=float(nav.iloc[-1]-1e7)
            nav.to_csv(TARGET/f'{key}_nav.csv')
            orders.to_csv(TARGET/f'{key}_orders.csv',index=False)
            row=dict(portfolio_stats(pf), strategy=name,fee_bp=bp,
                     ending_value_cny=float(nav.iloc[-1]),profit_cny=profit,
                     fees_cny=float(orders.Fees.sum()),
                     report_cagr=published[name][0],report_drawdown=published[name][1])
            rows.append(row)
            navs[key]=nav/1e7
            checks[key]={'accounting_passed':True,'fees_passed':True,
                         'next_session_execution_passed':True,'first_trade':str(orders.Timestamp.min())}
            print(key, 'CAGR', row['年化收益率'], 'DD',row['最大回撤'],flush=True)
    performance=pd.DataFrame(rows)
    performance.to_csv(TARGET/'industry_rrg_performance.csv',index=False)
    pd.DataFrame(navs).to_csv(TARGET/'industry_rrg_net_nav.csv')
    annual = {}
    for name, nav in navs.items():
        returns = nav.pct_change().fillna(0.)
        annual[name] = (1+returns).groupby(returns.index.year).prod()-1
    pd.DataFrame(annual).to_csv(TARGET/'industry_rrg_annual_returns.csv')
    write_json(TARGET/'validation.json',checks)
    spec={'upstream_commit':commit,'report':'西部证券2026-05-25，§2.1表1—4、§2.2及§3.1',
          'requested_period':[START,END],'warmup_start':WARMUP,
          'report_parameters':{'ratio':220,'momentum':60,'smoothing':20,'top_n':6},
          'industry_classification':'CITIC level 1 excluding 综合 and 综合金融',
          'implemented_branch':'Pure RRG industry-index portfolios; ETF replication gated by input audit.',
          'signal_prefix_invariance_passed': True,
          'explicit_implementation_conventions':[
              'Month-end signal, next-session open; report does not specify execution timestamp.',
              'Sell before buy; zeros liquidate deselected industries; initial cash CNY 10 million.',
              'Industry-index portfolios are hypothetical, not ETF holdings.',
              'Industry-layer fee unspecified in report: show zero and 3bp side by side.',
              '252-day annualization; no inference that report uses identical annualization.',
              'Report table 13 and table 16 give different monthly CAGR (21.51% vs 21.01%).'],
          'diffusion_etf_status':facts['strict_diffusion_etf_status']}
    write_json(TARGET/'run.json',spec)
    report=['研报设定复现：冻结后的独立实验','',
            f'回测区间 {START} 至 {END}；预热从 {WARMUP} 开始。',
            '已完成：研报§2.1纯RRG四个单象限和四种相邻象限，28个行业指数等权配置。',
            '所有信号/排序/执行函数复用未修改的开源实现。',
            '行业指数层费用和成交时点原文未说明：分别报告0/3bp、次日开盘的明确实施版本。',
            '这些是行业指数假设组合，不是可直接交易的ETF实盘收益。','',
            '各组收益见 industry_rrg_performance.csv，逐笔交易、权重、净值已保存。','',
            '扩散+ETF严格复现待解决的数据条件：']+facts['blockers']
    report+=['','年度行业归属覆盖：',pd.DataFrame(facts['annual_coverage']).to_string(index=False),
             '', '自由流通股本字段已经存在于本地daily_basic，先前“该数据不存在”的说法已更正。',
             '研报仅写全部持仓，没有确认使用PCF；PCF是补全数据的候选来源，不是已核实的原文事实。',
             '原有实验及数据库保留；冻结归档路径见 experiments_frozen/。',
             '严格ETF复现尚未完成，当前结果不得标为研报ETF收益复现成功。']
    (TARGET/'RESULTS.txt').write_text('\n'.join(report))
    verify_upstream()
    print('Completed report-period industry RRG and prerequisite audit:',TARGET,flush=True)


if __name__=='__main__':
    main()
