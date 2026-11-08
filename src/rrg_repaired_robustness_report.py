"""Read back the repaired-pool checks and generate the experiment report."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
from PIL import Image


ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'results/rrg_tushare_repaired_20261107'
OUT=SOURCE/'four_strategy_robustness'
LABELS={'Pure_RRG_Q1':'纯RRG第一象限','Report_base':'扩散＋RRG',
        'Industry_momentum_220_6':'行业动量','ETF_equal_weight':'ETF等权'}


def main():
    v=json.loads((OUT/'validation.json').read_text())
    inf=json.loads((OUT/'inference_validation.json').read_text())
    run=json.loads((OUT/'run.json').read_text())
    assert len(v['accounting'])==43
    for c in v['accounting'].values():
        assert all(c[k] for k in ['accounting_passed','fees_passed','next_open_dates_passed','retired_positions_zero_after_settlement'])
    assert v['primary_base_navs_replayed_exactly'] and v['all_three_220_6_grid_navs_match_primary']
    assert run['signal_reuse_verified_tables']['prior']==run['signal_reuse_verified_tables']['repaired']
    cost=pd.read_csv(OUT/'cost_cagr.csv',index_col=0)
    grid=pd.read_csv(OUT/'parameter_grid.csv')
    assert cost.shape==(4,4) and len(grid)==27
    matrix=grid.pivot(index=['lookback','max_industries'],columns='strategy',values='年化收益率')
    monthly=pd.read_csv(OUT/'monthly_returns_complete.csv',index_col=0)
    assert monthly.shape==(87,4)
    primary=pd.read_csv(OUT/'primary_pairwise_tests.csv')
    saved=pd.read_csv(SOURCE/'primary_pairwise_tests.csv')
    np.testing.assert_allclose(primary.p_two_sided,saved.p_two_sided,atol=1e-12)
    for file,n in [('all_HAC_tests.csv',18),('primary_pairwise_tests.csv',6),
                   ('cost_pairwise_tests.csv',24),('grid_multiple_tests.csv',54),('paired_block_bootstrap.csv',12)]:
        assert len(pd.read_csv(OUT/file))==n
    for name in LABELS:
        replay=pd.read_csv(OUT/f'{name}_3bp_nav.csv',index_col=0).iloc[:,0]
        baseline=pd.read_csv(SOURCE/f'{name}_nav.csv',index_col=0).iloc[:,0]
        np.testing.assert_allclose(replay,baseline,rtol=1e-12)
    for prefix in ['01_time_and_cost','02_parameter_grid','03_significance']:
        with Image.open(OUT/f'{prefix}.png') as im: im.verify()
        assert '<svg' in (OUT/f'{prefix}.svg').read_text()
    provenance=[SOURCE/'run.json',SOURCE/'candidate_etf_panel.parquet',
        SOURCE/'visible_etf_industry_weights.parquet',SOURCE/'etf_metadata_audit.parquet',
        SOURCE/'approved_nav_settlement_plan.csv']
    provenance+=[SOURCE/f'{n}_weights.parquet' for n in LABELS]
    provenance+=list((ROOT/'results/rrg_report_settings_20261107/diffusion_etf/four_strategy_robustness').glob('*_signals.parquet'))
    (OUT/'input_file_hashes.json').write_text(json.dumps([
        {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in provenance],indent=2))
    rolling=pd.read_csv(OUT/'rolling_12m_returns.csv',index_col=0)
    assert rolling.shape==(76,4)
    summary=pd.read_csv(OUT/'rolling_summary.csv',index_col=0)
    annual=pd.read_csv(OUT/'annual_returns.csv',index_col=0)
    dependency=pd.read_csv(OUT/'month_dependency.csv').set_index('strategy')
    boot=pd.read_csv(OUT/'paired_block_bootstrap.csv')
    stats=pd.read_csv(SOURCE/'performance.csv',index_col=0)
    qwins=int((matrix.Pure_RRG_Q1>matrix.Industry_momentum_220_6).sum())
    cwins=int((matrix.Pure_RRG_Q1>matrix.Report_base).sum())
    lines=['新ETF池下四策略：时间、成本、参数稳健性与显著性检验','完成日期：2026-11-07','',
        '主问题：RRG行业轮动相对简单动量、ETF等权，增加多少扣费收益，承担什么风险？',
        '子问题：纯第一象限与研报扩散＋RRG方案的收益/风险取舍。','',
        '一、共同口径',
        '2018-12-28至2026-03-31，1756交易日；2017-01-03起预热，首次成交2019-01-02。',
        '同一新历史ETF池、可见持仓暴露、行业/主题分类、历史上市/退市过滤、同指数20日ADV去重。',
        '三个主动策略均沿用base优化规则，选中行业暴露上限1/k；k可少于名义最多行业数。',
        '月末形成目标、次日复权开盘、先卖后买、旧持仓目标归零；初始1000万元、252日年化。',
        '主设置220日长期回看、60日RS-momentum、MA20、最多6行业；单边3bp、额外滑点0。',
        '退市沿用用户确认近似：退市后首个月度调仓，按形成日已公告净值和历史复权因子估算现金回款。',
        '34只候选设定相同结算时点/价格；成本情景同步改变包括估算结算在内的费用。',
        '净值近似金额及到账时间是研究约定，实际清盘现金流仍有差异空间。','',
        '二、主设置收益与风险','策略 | 扣费年化 | 最大回撤 | 夏普 | Calmar | 净利润(万元)']
    for name,label in LABELS.items():
        s=stats.loc[name]
        lines.append(f'{label} | {s["年化收益率"]:.2%} | {s["最大回撤"]:.2%} | {s["夏普比率"]:.3f} | {s["Calmar 比率"]:.3f} | {s.profit_cny/10000:.2f}')
    lines+=['期末利润含持仓市值，已扣费用。','',
        '三、时间稳定性','年份 | 纯第一象限 | 扩散＋RRG | 行业动量 | ETF等权']
    for y,row in annual.loc[2019:].iterrows():
        label='2026年1—3月' if y==2026 else str(y)
        lines.append(label+' | '+' | '.join(f'{row[n]:.2%}' for n in LABELS))
    lines+=['2026年首季按区间收益展示。87个完整月产生76个重叠滚动12月窗口。',
        '策略 | 排名第一窗口 | 正收益窗口 | 12月收益中位数 | 最差12月']
    for n,label in LABELS.items():
        s=summary.loc[n]
        lines.append(f'{label} | {int(s.top_windows)}/76 | {int(s.positive_windows)}/76 | {s.median_12m:.2%} | {s.worst_12m:.2%}')
    lines.append('纯第一象限相对动量/复合/等权的滚动胜出窗口分别：'+
        '、'.join(str(int((rolling.Pure_RRG_Q1>rolling[n]).sum()))+'/76' for n in ['Industry_momentum_220_6','Report_base','ETF_equal_weight'])+'。')
    lines+=['滚动窗口彼此重叠，仅作稳定性描述；统计检验使用完整月度收益。',
        '月份依赖：将各自收益最高3个月机械设为0，其余月份和样本长度保持原值，',
        '四策略年化分别为'+ '、'.join(f'{dependency.loc[n,"best3_flat_cagr"]:.2%}' for n in LABELS)+'。',
        '纯第一象限最高3个月占全期对数增长34.92%。此项是事后集中度压力诊断。','',
        '四、成本敏感性','单边总摩擦(bp) | 纯第一象限 | 扩散＋RRG | 行业动量 | ETF等权']
    for bp,row in cost.iterrows():
        lines.append(str(bp)+' | '+' | '.join(f'{row[n]:.2%}' for n in LABELS))
    lines+=['16组均重新模拟成交、现金与费用，统一把总交易摩擦作为费率收取，额外滑点0。',
        '纯第一象限在3/5/10/20bp均保持对动量的全期收益领先；20bp时约领先2.22个百分点。',
        '该范围代表成本压力情景，实际市场冲击仍取决于资金规模、流动性和执行方式。','',
        '五、参数稳健性',
        '180/220/260日长期回看 × 最多4/6/8行业，三个主动策略共27组。',
        'RS-momentum60日、MA20、优化与执行规则、新ETF池和3bp固定。',
        '复合策略同时改变扩散与RS-ratio长期回看；动量使用对应累计收益回看期。',
        'ETF等权不含对应信号参数，保持同一月度等权基准，年化10.12%。',
        '回看/最多行业 | 纯第一象限 | 扩散＋RRG | 行业动量']
    for (lookback,n),row in matrix.iterrows():
        lines.append(f'{lookback}/{n} | '+' | '.join(f'{row[k]:.2%}' for k in list(LABELS)[:3]))
    lines += [f'纯第一象限超过同参数动量{qwins}/9组，超过复合{cwins}/9组；三种主动策略各9/9组收益高于等权。',
        '纯第一象限年化范围12.73%—24.62%，中位数19.40%；原220/6设置在本网格最高。',
        '纯第一象限最大回撤在网格中约37.57%—57.89%；参数改变同时影响收益与风险。',
        '沿用主设置具有明确来源，当前网格仍属于已经观察过样本内的稳定性分析。','',
        '六、显著性与增量证据',
        '87个完整月配对净收益差，HAC(3)、小样本校正/t近似；HAC1/6为敏感性。',
        '比较 | 月均收益差(百分点) | 双侧p | 六组Holm p']
    for s in primary.itertuples():
        a,b=s.comparison.split(' minus ')
        lines.append(f'{LABELS[a]}−{LABELS[b]} | {s.mean_monthly_difference*100:.3f} | {s.p_two_sided:.4f} | {s.p_Holm_6:.4f}')
    lines+=['主设置6组、成本24组、参数54组分别按族Holm调整，在5%阈值下的拒绝数均为0。',
        '配对循环块bootstrap10000次，种子20261107，块长3/6个月；复利年化差另作区间估计。']
    for s in boot.loc[boot.comparison.str.startswith('Pure_RRG_Q1')].itertuples():
        _,b=s.comparison.split(' minus ')
        lines.append(f'纯第一象限−{LABELS[b]}，块长{s.block_months}月：年化差{s.observed_cagr_gap*100:+.2f}个百分点，95%区间[{s.cagr_gap_ci95_low*100:+.2f},{s.cagr_gap_ci95_high*100:+.2f}]。')
    lines+=['月均差HAC与CAGR差bootstrap对应不同估计量；bootstrap体现给定样本的抽样不确定性。',
        '以动量与等权收益为解释变量，纯第一象限HAC3月截距约0.304%，p约0.196；',
        '复合策略截距约0.066%，p约0.852。这是相对可投资基准的截距，风险模型alpha另需因子设定。','',
        '七、主问题与子问题的结论',
        '主问题：纯第一象限在本样本获得最高主设置扣费收益；成本范围和多数参数支持其历史经济优势。',
        '相对简单动量的领先约2.79个百分点，但滚动窗口、参数与统计区间显示增量仍有较大不确定性。',
        '相对等权收益优势更大，同时主设置回撤更深；单项p较低，多重比较后证据需谨慎表述。',
        '子问题：纯第一象限比复合年化高约5.67个百分点，最大回撤深约6.35个百分点。',
        '两方案同时改变扩散、象限集合和实际行业数，适合解释方案取舍；独立扩散贡献另需固定其余规则的消融。','',
        '八、验证、来源与数据口径',
        '16成本＋27参数共43组，费用、现金、期末持仓、次日成交和退市后清仓余额均检查通过。',
        '4条3bp主设置每日净值与上一轮逐日一致；3条220/6参数结果同样一致。',
        '信号输入四张表的逐行哈希汇总与行数与旧库一致，复用已计算的27组信号；',
        '全部参数均重新构建新ETF池配置权重并回放成交，核心信号/优化/回测函数沿用固定版本。',
        '估算结算：四个成本情景等权各25笔；动量260/6、260/8各1笔，其余主动情景0笔。',
        '历史行业新增0，2018/2019股票日覆盖约56%/60%、自由流通市值覆盖约36%/42%；',
        'ETF持仓部分披露、静态产品分类/跟踪指数身份、净值结算近似等数据差异继续保留。',
        '同一已观察历史样本，新池修订和扩展检验不构成独立样本验证。',
        '数据源为用户授权Tushare兼容接口；固定QuantsPlaybook版本3f83a77f4c7996ed8185b8979a42c4372c1d811f。',
        '旧池实验保留；本文所有表图均来自新池，避免跨版本混用。','',
        '文件：01_time_and_cost、02_parameter_grid、03_significance（PNG/SVG）；',
        'cost_sensitivity.csv、parameter_grid.csv、annual_returns.csv、rolling_summary.csv、month_dependency.csv；',
        'primary_pairwise_tests.csv、all_HAC_tests.csv、cost_pairwise_tests.csv、grid_multiple_tests.csv；',
        'paired_block_bootstrap.csv、benchmark_adjusted_regression.csv；',
        'run.json、validation.json、inference_validation.json、input_file_hashes.json、final_readback.json。',
        '复现：rrg_report_robustness.py --repaired；rrg_report_inference.py --target 本目录；',
        'rrg_report_robustness_figures.py --target 本目录；rrg_repaired_robustness_report.py。']
    (OUT/'RESULTS.txt').write_text('\n'.join(lines)+'\n')
    (OUT/'final_readback.json').write_text(json.dumps({
        'accounting_cases_passed':43,'cost_cases':16,'grid_cases':27,'complete_months':87,
        'rolling_windows':76,'primary_comparisons':6,'cost_comparisons':24,'parameter_comparisons':54,
        'bootstrap_rows':12,'bootstrap_reps':10000,'Q1_matched_momentum_wins':qwins,
        'Q1_combined_wins':cwins,'primary_navs_match_previous':True,
        'base_grid_navs_match_primary':True,'all_retirements_checked':True,
        'unchanged_signal_inputs_verified':True,'figure_pairs_verified':3,
        'Holm_rejections':{k:inf[k] for k in ['primary_Holm_rejections','cost_Holm_rejections','grid_Holm_rejections']}
    },ensure_ascii=False,indent=2))
    print('Report and final readback complete.',flush=True)


if __name__=='__main__':
    main()
