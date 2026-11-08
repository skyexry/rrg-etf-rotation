"""Summarize the repaired-pool comparison, keeping earlier findings separate."""
from itertools import combinations
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

from rrg_mechanism_statistics import hac

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/rrg_tushare_repaired_20261107'
OLD=ROOT/'results/rrg_report_settings_20261107/diffusion_etf/pure_q1_comparison'
LABELS={'Pure_RRG_Q1':'纯RRG第一象限','Report_base':'扩散＋RRG',
        'Industry_momentum_220_6':'行业动量','ETF_equal_weight':'ETF等权'}


def main():
    stats=pd.read_csv(OUT/'performance.csv',index_col=0)
    old=pd.read_csv(OLD/'performance.csv',index_col=0)
    checks=json.loads((OUT/'validation.json').read_text())
    assert checks['status']=='approved_nav_approximation_complete'
    assert checks['retirement_positions_needing_review']==0
    assert all(all(c[k] for k in ['accounting_passed','fees_passed','next_open_dates_passed'])
               for c in checks['accounting'].values())
    nav=pd.read_csv(OUT/'net_nav.csv',index_col=0,parse_dates=True)
    assert nav.shape==(1756,4) and nav.notna().all().all()
    raw_files=list((ROOT/'data/rrg/repair_20261107/raw').rglob('*.parquet'))
    for f in raw_files:
        manifest=json.loads(f.with_suffix('.json').read_text())
        assert hashlib.sha256(f.read_bytes()).hexdigest()==manifest['sha256']
    comparison=[]
    for name in LABELS:
        comparison.append({'strategy':name,'label':LABELS[name],
            'old_cagr':old.loc[name,'年化收益率'],'new_cagr':stats.loc[name,'年化收益率'],
            'cagr_change_pp':100*(stats.loc[name,'年化收益率']-old.loc[name,'年化收益率']),
            'old_max_drawdown':old.loc[name,'最大回撤'],'new_max_drawdown':stats.loc[name,'最大回撤'],
            'new_sharpe':stats.loc[name,'夏普比率'],'new_calmar':stats.loc[name,'Calmar 比率'],
            'new_net_profit_cny':stats.loc[name,'profit_cny']})
    comparison=pd.DataFrame(comparison)
    comparison.to_csv(OUT/'before_after_comparison.csv',index=False)
    r=pd.read_csv(OUT/'monthly_returns_complete.csv',index_col=0,parse_dates=True)
    tests=pd.DataFrame([hac((r[a]-r[b]).to_numpy(),a+' minus '+b,3)
                        for a,b in combinations(LABELS,2)])
    tests['p_Holm_6']=multipletests(tests.p_two_sided,method='holm')[1]
    tests.to_csv(OUT/'primary_pairwise_tests.csv',index=False)
    trades=pd.read_csv(OUT/'ETF_equal_weight_estimated_settlement_transactions.csv')
    plan=pd.read_csv(OUT/'approved_nav_settlement_plan.csv')
    assert len(trades)==trades.Column.nunique()
    assert len(plan)==plan.code.nunique()
    for row in plan.itertuples():
        assert row.nav_ann_date<=row.formation_date<row.execution_date
        assert row.factor_date<=row.nav_date<=row.formation_date
    assert np.allclose(trades.Fees,trades.Size*trades.Price*.0003)
    coverage=pd.read_csv(OUT/'historical_membership_coverage.csv')
    meta=pd.read_parquet(OUT/'etf_metadata_audit.parquet')
    pool=pd.read_csv(OUT/'effective_candidate_history.csv')
    counts=pool.groupby('date').size()
    quote_audit=pd.read_csv(OUT/'retirement_data_query_audit.csv')
    assert quote_audit.additional_price_dates.eq(0).all()
    evidence={'four_strategy_accounting_passed':True,'first_trade_all':'2019-01-02',
        'full_period_days':1756,'complete_months':len(r),'industry_intervals_added':0,
        'industry_theme_etfs':int(meta.eligible.sum()),
        'currently_delisted_eligible':int((meta.eligible&meta.list_status.eq('D')).sum()),
        'effective_delisted_candidates':int(pool.loc[pool.currently_delisted,'code'].nunique()),
        'pool_first':int(counts.iloc[0]),'pool_last':int(counts.iloc[-1]),
        'settlement_plans':len(plan),'actual_estimated_settlements':len(trades),
        'ann_date_and_factor_timing_passed':True,
        'no_open_retired_positions_after_settlement':True,
        'supplemental_raw_checksums_passed':len(raw_files),
        'primary_HAC_pairs':len(tests),'Holm_5pct_rejections':int(tests.p_Holm_6.lt(.05).sum())}
    assert len(r)==87 and len(tests)==6
    assert stats.first_trade.str.startswith('2019-01-02').all()
    (OUT/'final_readback.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=Path('/Library/Fonts/Arial Unicode.ttf')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
                         'axes.unicode_minus':False,'svg.fonttype':'path'})
    fig,axes=plt.subplots(2,1,figsize=(11,8),sharex=True,gridspec_kw={'height_ratios':[1.6,1]})
    colors=['#205F91','#9468A0','#D07E36','#628E7C']
    for (name,label),color in zip(LABELS.items(),colors):
        axes[0].plot(nav.index,nav[name],color=color,label=f'{label}：年化{stats.loc[name,"年化收益率"]:.2%}')
        axes[1].plot(nav.index,(nav[name]/nav[name].cummax()-1)*100,color=color)
    axes[0].legend(frameon=False)
    axes[0].set(ylabel='净值（初始＝1）',title='修订历史ETF池后：四策略统一扣费比较')
    axes[1].set(ylabel='回撤（%）')
    for ax in axes: ax.grid(alpha=.2)
    fig.text(.07,.025,'2018-12-28—2026-03-31｜220日／最多6行业；月度、次日开盘、先卖后买、单边3bp。\n退市基金按事先确认的已披露净值近似结算；早期行业归属覆盖缺口继续披露。',fontsize=9)
    fig.tight_layout(rect=(0,.08,1,1))
    for suffix in ['png','svg']: fig.savefig(OUT/f'comparison.{suffix}',dpi=170)
    plt.close(fig)
    lines=['历史行业归属核查与ETF候选池修订后的四策略统一比较','2026-11-07','',
        '完成范围：Tushare兼容接口补查历史行业、ETF身份、退市状态、指数分类和退市基金净值；重跑原四策略。',
        '原始数据、旧窗口、旧结果全部保留；本目录作为新的数据口径版本。','',
        '一、补数与数据修订',
        '历史中信归属：全市场Y/N、30个一级行业分别Y/N、30只早期缺失股票分别Y/N，',
        '共134个查询任务；6,750条归属区间与原始记录一致，新增0。',
        '2018/2019股票日覆盖仍为55.93%/59.97%，自由流通市值加权覆盖为35.55%/41.89%。',
        '2020起对应市值覆盖约98.66%以上。缺失记录未使用当前行业回填。',
        'ETF元数据：更新L/D名单，用同基金六位代码匹配.OF记录补指数字段，修复248个字段。',
        f'按Tushare index_basic行业/主题类别筛选，共{evidence["industry_theme_etfs"]}只符合样本日期的ETF，其中{evidence["currently_delisted_eligible"]}只目前已退市。',
        '分类标签为当前目录标签；历史产品/指数更名与分类变更仍缺完整时点记录。',
        '明示CNI与SZ后缀映射，其余未知分类列入审计表；不按名称主观猜类别。',
        f'再按当时上市/退市日期、实际行情、可见持仓、境外占比及同指数20日ADV去重，',
        f'有效池从首期{counts.iloc[0]}只到末期{counts.iloc[-1]}只，共出现过{evidence["effective_delisted_candidates"]}只当前已退市ETF。',
        '718/54为全样本静态资格数量；每月真正可配置的数量随时间与持仓披露变化。','',
        '二、统一规则',
        '2018-12-28至2026-03-31，1756交易日；2017-01-03起预热，首次交易均为2019-01-02。',
        'RRG220/60/MA20，最多6行业；动量220日/前6；月度调仓、次日复权开盘、先卖后买、显式退出。',
        '单边3bp、额外滑点0、本金1000万元、252日年化。主动策略沿用base优化与行业上限1/k。',
        '三个主动信号与原数据口径一致；变化来自ETF候选池、身份字段及实际配置。','',
        '三、退市处理（用户已确认的近似）',
        '停牌期间按最后可得复权价格估值；退市后的首个月末，用当时已公告的最新单位净值，',
        '乘对应历史复权因子，在下个交易日模拟转回现金，按3bp计费用。',
        '未来披露的修订净值不回填。该结算金额和日期是研究近似，不是实际清盘到账证明。',
        f'对{len(plan)}只历史候选准备净值结算价格；等权实际发生{len(trades)}笔估算结算。',
        '三个主动组合的净值与加入结算前一致。所有结算完成后相关持仓余额归零。',
        '单基金行情补查均未返回晚于原最后报价日的新成交行情；fund_nav的一个分页异常已按月/日拆分核实。','',
        '四、补数前后年化（百分点变化）']
    for row in comparison.itertuples():
        lines.append(f'{row.label}: {row.old_cagr:.2%} → {row.new_cagr:.2%}；变化{row.cagr_change_pp:+.2f}个百分点')
    lines+=['','五、新版本收益风险与净利润','策略 | 年化 | 最大回撤 | 波动率 | 夏普 | Calmar | 净利润(万元)']
    for name,label in LABELS.items():
        s=stats.loc[name]
        lines.append(f'{label} | {s["年化收益率"]:.2%} | {s["最大回撤"]:.2%} | {s["年化波动率"]:.2%} | {s["夏普比率"]:.3f} | {s["Calmar 比率"]:.3f} | {s.profit_cny/10000:.2f}')
    lines+=['利润含期末持仓市值；已扣交易与估算清盘费用。','',
        '六、更新主设置显著性',
        '87个完整月，配对月均净收益差，双侧HAC(3)、小样本校正/t近似；六组比较Holm校正。']
    for row in tests.itertuples():
        a,b=row.comparison.split(' minus ')
        lines.append(f'{LABELS[a]} − {LABELS[b]}: 月均差{row.mean_monthly_difference*100:.3f}个百分点；p={row.p_two_sided:.4f}；Holm p={row.p_Holm_6:.4f}')
    lines+=['这些检验对应当前新池；旧版本的参数网格、成本敏感性与bootstrap结果仅适用于旧池。',
        '当前样本已用于探索，检验不构成独立样本验证。','',
        '七、如何解释变化',
        '本轮历史行业归属新增为0，所以收益变化不能解释为早期行业已经补齐。',
        '本轮同时调整了行业/主题分类筛选、退市ETF纳入、指数代码修复及可交易状态；收益差是这些修订的合计效果。',
        '等权还包含已明确批准的净值结算近似；三个主动策略没有发生此类结算。',
        '早期行业缺口、ETF持仓仅部分披露、静态产品分类/跟踪指数身份等差异继续保留。',
        '这仍是可获取数据下的方法比较，不能称为完整商业数据库的逐项严格复刻。','',
        '来源：用户授权的Tushare兼容中转接口；当前接口与官方商业数据口径一致性未独立核验。',
        '核心信号/优化/回测复用未修改QuantsPlaybook，固定版本3f83a77f4c7996ed8185b8979a42c4372c1d811f。',
        '脚本：src/rrg_repair_download.py、rrg_repair_inputs.py、rrg_repair_retirement_download.py、rrg_repair_backtest.py、rrg_repair_summary.py。',
        '数据：data/rrg/repair_20261107/raw/；查询参数、抓取时间和SHA256随各Parquet保存。',
        '复现顺序：download → inputs → backtest（退出审计）→ retirement_download → backtest --settle → summary。']
    (OUT/'RESULTS.txt').write_text('\n'.join(lines)+'\n')
    print(comparison.to_string(index=False));print(tests.to_string(index=False));print(json.dumps(evidence,ensure_ascii=False))


if __name__=='__main__':
    main()
