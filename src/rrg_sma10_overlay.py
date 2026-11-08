"""Apply one shared monthly 10-month trend gate to repaired-pool portfolios."""
import json

import numpy as np
import pandas as pd

from rrg_pipeline import ROOT, bind_upstream, verify_upstream, write_json
from rrg_report_tushare import START, END, WARMUP, PARENT, sha, validate
from rrg_repair_inputs import DB, OUT as SOURCE
from rrg_repair_backtest import settlement_prices

OUT=SOURCE/'sma10_filter'
LABELS={'Pure_RRG_Q1':'纯第一象限', 'Pure_RRG_Q1_SMA10':'纯第一象限＋10月均线',
        'Industry_momentum_220_6':'行业动量', 'Industry_momentum_220_6_SMA10':'行业动量＋10月均线'}


def month_end_gate(benchmark):
    monthly=benchmark.groupby(benchmark.index.to_period('M')).tail(1)
    average=monthly.rolling(10,min_periods=10).mean()
    return pd.DataFrame({'benchmark_close':monthly,'SMA10':average,
                         'risk_on':monthly.gt(average) & average.notna()})


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    config=json.loads((SOURCE/'run.json').read_text())
    assert sha(DB)==config['input_sha256']
    bind_upstream(DB)
    from src.data_provider import fetch_rrg_dataset,fetch_etf_daily
    from src.backtest import run_vbt_backtest,portfolio_stats
    import vectorbt as vbt

    benchmark,_,_=fetch_rrg_dataset(start_date=WARMUP,end_date=END,classification='zx')
    reference=pd.read_csv(PARENT/'industry_equal_weight_daily_benchmark.csv',index_col=0,parse_dates=True).iloc[:,0]
    np.testing.assert_allclose(benchmark.reindex(reference.index),reference,rtol=1e-12)
    gate=month_end_gate(benchmark)
    cutoff=pd.Timestamp('2022-12-30')
    pd.testing.assert_frame_equal(gate.loc[:cutoff],month_end_gate(benchmark.loc[:cutoff]))
    base_names=['Pure_RRG_Q1','Industry_momentum_220_6']
    # Keep the established column order and same available price grid as the
    # four-strategy experiment, including approved NAV retirement estimates.
    prior_weights={n:pd.read_parquet(SOURCE/f'{n}_weights.parquet') for n in config['primary_strategies']}
    cols=sorted(set().union(*(set(w.columns) for w in prior_weights.values())))
    dates=prior_weights[base_names[0]].index
    selected=gate.reindex(dates)
    assert selected.SMA10.notna().all() and selected.benchmark_close.notna().all()
    assert selected.index.equals(dates)
    selected.to_csv(OUT/'monthly_market_filter.csv')
    benchmark.to_csv(OUT/'industry_equal_weight_benchmark.csv')
    weights={}
    for n in base_names:
        w=prior_weights[n].reindex(columns=cols).fillna(0.)
        weights[n]=w
        weights[n+'_SMA10']=w.mul(selected.risk_on.astype(float),axis=0)
    close,open_,_=fetch_etf_daily(cols,START,END,lookback=25)
    close,open_=close[cols].loc[START:],open_[cols].loc[START:]
    meta=pd.read_parquet(SOURCE/'etf_metadata_audit.parquet').set_index('ts_code')
    close,open_,plans=settlement_prices(prior_weights['ETF_equal_weight'].reindex(columns=cols).fillna(0.),
        close,open_,meta,save_plan_to=OUT/'approved_nav_settlement_plan.csv')
    daily_gate=selected.risk_on.astype(float).reindex(close.index).ffill().shift(1).fillna(0.)
    daily_gate.to_csv(OUT/'effective_daily_market_filter.csv')
    original=vbt.settings.portfolio['call_seq'];vbt.settings.portfolio['call_seq']='auto'
    rows,checks,navs=[],{},{}
    try:
        for name,w in weights.items():
            pf=run_vbt_backtest(w,close,open_,fees=.0003,slippage=0.,init_cash=1e7)
            check=validate(pf,w,close,.0003)
            orders=pf.orders.records_readable
            signed=orders.Size.where(orders.Side.eq('Buy'),-orders.Size)
            shares=orders.assign(signed=signed).pivot_table(index='Timestamp',columns='Column',values='signed',aggfunc='sum')
            shares=shares.reindex(index=close.index,columns=cols).fillna(0.).cumsum()
            for p in plans:
                assert shares.loc[p['execution_date']:,p['code']].abs().max()<1e-5
            if name in base_names:
                prior=pd.read_csv(SOURCE/f'{name}_nav.csv',index_col=0).iloc[:,0]
                np.testing.assert_allclose(pf.value().to_numpy(),prior.to_numpy(),rtol=1e-12)
                check['prior_nav_replayed_exactly']=True
            else:
                assert w.loc[~selected.risk_on].abs().max().max()==0
                risky=(shares*close.ffill()).fillna(0.).sum(axis=1)
                assert risky.loc[daily_gate.eq(0)].abs().max()<1e-4
                check['risk_off_positions_zero']=True
                check['shared_market_gate']=True
            before=pf.value().shift(1).fillna(1e7)
            metrics=dict(portfolio_stats(pf),strategy=name,**check,
                annualized_two_way_turnover=float((orders.Size*orders.Price/orders.Timestamp.map(before)).sum()*252/len(close)),
                mean_cash_fraction=float((pf.cash()/pf.value()).mean()))
            rows.append(metrics);checks[name]=check;navs[name]=pf.value()/1e7
            w.to_parquet(OUT/f'{name}_weights.parquet')
            pf.value().to_csv(OUT/f'{name}_nav.csv')
            orders.to_csv(OUT/f'{name}_orders.csv',index=False)
            print(name,metrics['年化收益率'],metrics['最大回撤'],flush=True)
    finally:
        vbt.settings.portfolio['call_seq']=original
    stats=pd.DataFrame(rows).set_index('strategy');stats.to_csv(OUT/'performance.csv')
    navs=pd.DataFrame(navs);navs.to_csv(OUT/'net_nav.csv')
    monthly=navs.resample('ME').last().pct_change(fill_method=None)
    monthly.iloc[0]=navs.resample('ME').last().iloc[0]-1
    monthly.to_csv(OUT/'monthly_returns.csv')
    annual=(1+monthly).groupby(monthly.index.year).prod()-1
    annual.to_csv(OUT/'annual_returns.csv')
    drawdowns=[]
    for name in navs:
        series=navs[name];dd=series/series.cummax()-1
        trough=dd.idxmin();peak=series.loc[:trough].idxmax()
        drawdowns.append({'strategy':name,'peak_date':str(peak.date()),'trough_date':str(trough.date()),
                          'max_drawdown':float(dd.loc[trough]),'peak_nav':float(series.loc[peak]),
                          'trough_nav':float(series.loc[trough])})
    pd.DataFrame(drawdowns).to_csv(OUT/'drawdown_periods.csv',index=False)
    attribution=monthly.copy()
    for name in base_names:
        attribution[name+'_filter_minus_original']=monthly[name+'_SMA10']-monthly[name]
    attribution.to_csv(OUT/'monthly_filter_comparison.csv')
    pairs=[]
    for n in base_names:
        new=n+'_SMA10';a,b=stats.loc[new],stats.loc[n]
        pairs.append({'strategy':n,'cagr_change_pp':100*(a['年化收益率']-b['年化收益率']),
            'max_drawdown_improvement_pp':100*(a['最大回撤']-b['最大回撤']),
            'CAGR_retained_ratio':a['年化收益率']/b['年化收益率'],
            'profit_change_cny':a.profit_cny-b.profit_cny,'fees_change_cny':a.fees_cny-b.fees_cny})
    pd.DataFrame(pairs).to_csv(OUT/'filter_effect.csv',index=False)
    switches=selected.risk_on.ne(selected.risk_on.shift(1))
    selected.loc[switches].to_csv(OUT/'market_regime_changes.csv')
    run=dict(config,task='Shared industry-benchmark monthly SMA10 filter for Q1 and momentum.',
        benchmark='Upstream daily-rebalanced equal-weight returns of 28 CITIC level-1 industries; exclude comprehensive/comprehensive financial.',
        filter='At actual last trading close each month: benchmark > arithmetic mean of latest 10 month-end values, including current month. Else all ETF target weights zero.',
        timing='Signal at month-end close, orders next trading open; unchanged monthly schedule.',
        cash_return=0.,allow_borrowing=False,filter_window_months=10,primary_strategies=list(weights),
        execution_months=len(selected)-1,risk_on_formation_months=int(selected.risk_on.iloc[:-1].sum()),
        effective_risk_on_days=int(daily_gate.sum()),effective_trading_days=len(close),
        methodology='Faber-style absolute trend overlay adapted to the preexisting industry benchmark; not a new ETF selection rule.',
        retirement_plans=len(plans),source_database_sha256=sha(DB),
        vendor_unchanged=verify_upstream(),previous_results_preserved=True)
    write_json(OUT/'run.json',run)
    write_json(OUT/'validation.json',{'accounting':checks,'benchmark_matches_existing_rrg_benchmark':True,
        'prefix_invariance_passed':True,'complete_10_month_warmup':True,
        'same_gate_for_both_strategies':True,'zero_risky_holdings_when_filter_off':True,
        'unfiltered_daily_navs_match_prior':True,'retirements_checked':True})

    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from pathlib import Path
    font=Path('/Library/Fonts/Arial Unicode.ttf');font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
                         'axes.unicode_minus':False,'svg.fonttype':'path'})
    fig,axes=plt.subplots(2,1,figsize=(11,8),sharex=True,gridspec_kw={'height_ratios':[1.6,1]})
    colors=['#205F91','#205F91','#D07E36','#D07E36']
    for (name,label),color in zip(LABELS.items(),colors):
        style='-' if name.endswith('_SMA10') else '--'
        axes[0].plot(navs.index,navs[name],style,color=color,label=f'{label}：年化{stats.loc[name,"年化收益率"]:.2%}')
        axes[1].plot(navs.index,(navs[name]/navs[name].cummax()-1)*100,style,color=color)
    for ax in axes:
        ax.fill_between(close.index,0,1,where=daily_gate.eq(0),transform=ax.get_xaxis_transform(),color='grey',alpha=.12)
        ax.grid(alpha=.2)
    axes[0].legend(frameon=False);axes[0].set(ylabel='净值（初始＝1）',title='同一市场趋势过滤：收益与回撤如何变化？')
    axes[1].set(ylabel='回撤（%）')
    fig.text(.07,.02,'2018-12-28—2026-03-31｜新ETF池，单边3bp；灰区为均线过滤后的持币期。\n月末信号、次日开盘；现金收益0；退市沿用已确认净值近似。虚线为未过滤，实线为过滤。',fontsize=9)
    fig.tight_layout(rect=(0,.08,1,1))
    for suffix in ['png','svg']:fig.savefig(OUT/f'comparison.{suffix}',dpi=170)
    plt.close(fig)
    lines=['新ETF池：纯第一象限与行业动量的共同10个月均线过滤','2026-11-07','',
        '规则事先固定：月末用原RRG中信28行业等权基准判断绝对趋势；',
        '基准高于含当前月在内最近10个月末均值，按原策略ETF目标配置，否则目标全零持现金。',
        '次日开盘执行；现金收益0；不加杠杆；买卖各3bp；额外滑点0。',
        '220日/最多6行业和原优化规则保持固定；两策略使用同一个市场开关。',
        '信号预热从2017年开始，月末日期按实际最后交易日，避免把月末标签错当信息日期。','',
        '策略 | 扣费年化 | 最大回撤 | 波动率 | 夏普 | Calmar | 净利润(万元) | 累计费用(万元) | 平均持币比例']
    for n,label in LABELS.items():
        s=stats.loc[n]
        lines.append(f'{label} | {s["年化收益率"]:.2%} | {s["最大回撤"]:.2%} | {s["年化波动率"]:.2%} | {s["夏普比率"]:.3f} | {s["Calmar 比率"]:.3f} | {s.profit_cny/10000:.2f} | {s.fees_cny/10000:.2f} | {s.mean_cash_fraction:.2%}')
    lines+=['','过滤的收益与回撤取舍']
    for p in pairs:
        change=p['max_drawdown_improvement_pp']
        description=f'最大回撤减轻{change:.2f}' if change>=0 else f'最大回撤加深{-change:.2f}'
        lines.append(f'{LABELS[p["strategy"]]}：年化变化{p["cagr_change_pp"]:+.2f}个百分点；{description}个百分点；年化比率（新/旧）{p["CAGR_retained_ratio"]:.1%}。')
    q=stats.loc['Pure_RRG_Q1_SMA10','年化收益率']
    lines+=['',f'过滤后纯第一象限对未过滤动量年化差：{(q-stats.loc["Industry_momentum_220_6","年化收益率"])*100:+.2f}个百分点。',
        f'过滤后纯第一象限对同样过滤动量年化差：{(q-stats.loc["Industry_momentum_220_6_SMA10","年化收益率"])*100:+.2f}个百分点。',
        f'87个有实际持有期的形成月中，开关开启{run["risk_on_formation_months"]}个月；实际风险开启{run["effective_risk_on_days"]}/{len(close)}交易日。','',
        '分年度（2026只含1—3月）','年份 | 纯第一象限 | 过滤纯第一象限 | 动量 | 过滤动量']
    for y,s in annual.loc[2019:].iterrows(): lines.append(str(y)+' | '+' | '.join(f'{s[n]:.2%}' for n in LABELS))
    lines+=['','典型案例：2024年9—10月的延迟入场',
        '2024-08-30基准低于10月均线，9月持币；2024-09-30转为开启，2024-10-08开盘重新入场。',
        '纯第一象限9月未过滤收益+17.58%，过滤后0；10月未过滤+0.53%，过滤后-9.78%。',
        '动量9月未过滤+13.27%，过滤后0；10月未过滤-1.24%，过滤后-9.97%。',
        '这一案例展示错过反弹、随后入场的代价；全部月份差异保存在monthly_filter_comparison.csv。',
        '过滤后两组合最大回撤谷底均为2025-04-07，峰值日期为2021-09-13。',
        '低波动并不等于更浅最大回撤；这条固定规则在本样本的收益/回撤表现更差。']
    lines+=['','核验：两条未过滤净值逐日重现旧结果；两条过滤策略关闭期股票持仓归零；',
        '费用、现金、次日交易日期、期末资产和退市后余额核对通过；过滤信号前缀不受未来数据影响。',
        '原数据覆盖与净值近似等差异沿用；当前是已经观察历史样本的延伸实验。',
        '本实验只测试事先指定的10个月均线，属于把文献趋势过滤方法应用于RRG基准。',
        '来源：https://mebfaber.com/2017/12/13/episode-86-quantitative-approach-tactical-asset-allocation/',
        '核心信号、优化和回测沿用QuantsPlaybook固定版本；本脚本仅在已有权重上施加共同市场开关。',
        '文件：performance.csv、filter_effect.csv、monthly_market_filter.csv、effective_daily_market_filter.csv、',
        'market_regime_changes.csv、annual_returns.csv、monthly_returns.csv、monthly_filter_comparison.csv、drawdown_periods.csv、',
        '各组合权重/净值/成交、comparison.png/svg、run.json、validation.json。',
        '复现：Factor_Replication/.venv_rrg/bin/python Factor_Replication/src/rrg_sma10_overlay.py。']
    (OUT/'RESULTS.txt').write_text('\n'.join(lines)+'\n')
    print(stats.to_string(),flush=True)


if __name__=='__main__':
    main()
