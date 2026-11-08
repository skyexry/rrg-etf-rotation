"""Figures for the completed unified report-window checks."""
import argparse
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd


def main(target=None):
    target=Path(target) if target is not None else Path(__file__).resolve().parents[1]/'results/rrg_report_settings_20261107/diffusion_etf/four_strategy_robustness'
    run=json.loads((target/'run.json').read_text())
    font=Path('/Library/Fonts/Arial Unicode.ttf')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
        'font.size':11,'axes.titlesize':14,'axes.unicode_minus':False,'svg.fonttype':'path',
        'axes.spines.top':False,'axes.spines.right':False})
    labels={'Pure_RRG_Q1':'纯 RRG 第一象限','Report_base':'扩散＋RRG',
        'Industry_momentum_220_6':'行业动量','ETF_equal_weight':'ETF 等权'}
    colors=['#205F91','#9468A0','#D07E36','#628E7C']
    annual=pd.read_csv(target/'annual_returns.csv',index_col=0).loc[2019:]
    rolling=pd.read_csv(target/'rolling_12m_returns.csv',index_col=0,parse_dates=True)
    cost=pd.read_csv(target/'cost_cagr.csv',index_col=0)
    fig=plt.figure(figsize=(14,10))
    gs=fig.add_gridspec(2,2)
    ax=fig.add_subplot(gs[0,:]);x=np.arange(len(annual));width=.19
    for i,((name,label),color) in enumerate(zip(labels.items(),colors)):
        ax.bar(x+(i-1.5)*width,annual[name]*100,width,label=label,color=color)
    ax.set(xticks=x,xticklabels=[str(y) if int(y)!=2026 else '2026年1—3月' for y in annual.index],
        ylabel='区间收益（%）',title='分年度表现：领先随市场阶段变化')
    ax.legend(frameon=False,ncol=4,loc='upper right');ax.axhline(0,color='grey',linewidth=.8);ax.grid(axis='y',alpha=.2)
    ax=fig.add_subplot(gs[1,0])
    for (name,label),color in zip(labels.items(),colors):
        ax.plot(rolling.index,rolling[name]*100,label=label,color=color)
    ax.axhline(0,color='grey',linewidth=.8)
    ax.set(title='76个滚动12个月窗口',ylabel='12个月收益（%）')
    ax.legend(frameon=False,fontsize=9);ax.grid(alpha=.2)
    ax=fig.add_subplot(gs[1,1])
    gap=(cost['Pure_RRG_Q1']-cost['Industry_momentum_220_6'])*100
    for (name,label),color in zip(labels.items(),colors):
        ax.plot(cost.index,cost[name]*100,label=label,color=color,marker='o')
        offset={'Pure_RRG_Q1':-12,'Industry_momentum_220_6':12}.get(name,0) if abs(gap.loc[20])<.6 else 0
        ax.annotate(f'{cost.loc[20,name]*100:.2f}%',(20,cost.loc[20,name]*100),
                    xytext=(7,offset),textcoords='offset points',color=color,fontsize=9,va='center')
    cost_title='成本上升：第一象限在所测成本下仍领先动量' if gap.gt(0).all() else '成本上升：第一象限与动量排名可反转'
    ax.set(title=cost_title,xlabel='单边总交易摩擦（bp）',ylabel='扣费年化（%）',xticks=[3,5,10,20],xlim=(2,23))
    ax.grid(alpha=.2)
    fig.suptitle('四策略统一比较：时间稳定性与交易成本',fontsize=20)
    note='2018-12-28—2026-03-31｜原220日／6行业设置；次日开盘、先卖后买。滚动窗口从完整2019年1月起算。'
    if run.get('retirement_approximation_plans',0): note+='\n退市基金沿用已确认的披露净值结算近似。'
    fig.text(.05,.015,note,fontsize=10)
    fig.tight_layout(rect=(0,.055,1,.96),h_pad=3)
    fig.savefig(target/'01_time_and_cost.png',dpi=170)
    fig.savefig(target/'01_time_and_cost.svg')
    plt.close(fig)

    grid=pd.read_csv(target/'parameter_grid.csv')
    fig,axes=plt.subplots(1,3,figsize=(14,4.5),sharey=True)
    for ax,name in zip(axes,list(labels)[:3]):
        values=grid.loc[grid.strategy.eq(name)].pivot(index='lookback',columns='max_industries',values='年化收益率')*100
        ax.imshow(values,aspect='auto',cmap='YlGnBu',vmin=grid['年化收益率'].min()*100,vmax=grid['年化收益率'].max()*100)
        ax.set(title=labels[name],xlabel='最多行业数',xticks=range(3),xticklabels=values.columns,yticks=range(3),yticklabels=values.index)
        for i in range(3):
            for j in range(3):
                ax.text(j,i,f'{values.iloc[i,j]:.2f}%',ha='center',va='center',
                    bbox={'facecolor':'white','alpha':.65,'edgecolor':'none','pad':2})
        ax.add_patch(Rectangle((.5,.5),1,1,fill=False,edgecolor='black',linewidth=2))
    axes[0].set_ylabel('长期回看期（交易日）')
    fig.suptitle('参数稳健性：扣费年化（%），黑框为原220／6设置',fontsize=17)
    fig.text(.05,.015,f'同一数据、候选池、优化规则及3bp费率。ETF等权不含这两个信号参数，固定年化{cost.loc[3,"ETF_equal_weight"]:.2%}。全部网格完整报告。',fontsize=10)
    fig.tight_layout(rect=(0,.065,1,.93))
    fig.savefig(target/'02_parameter_grid.png',dpi=170)
    fig.savefig(target/'02_parameter_grid.svg')
    plt.close(fig)

    tests=pd.read_csv(target/'primary_pairwise_tests.csv')
    fig,ax=plt.subplots(figsize=(13,6))
    lower=float(tests.ci95_low.min()*100);upper=float(tests.ci95_high.max()*100)
    span=upper-lower
    for i,row in tests.reset_index(drop=True).iterrows():
        a,b=row.comparison.split(' minus ')
        mu,lo,hi=[row[k]*100 for k in ['mean_monthly_difference','ci95_low','ci95_high']]
        ax.errorbar(mu,i,xerr=np.array([[mu-lo],[hi-mu]]),fmt='o',capsize=5,color='#205F91')
        ax.text(upper+span*.08,i,f'p={row.p_two_sided:.3f}；Holm={row.p_Holm_6:.3f}',va='center',fontsize=10)
    ax.set(yticks=range(len(tests)),yticklabels=[labels[c.split(' minus ')[0]]+' − '+labels[c.split(' minus ')[1]] for c in tests.comparison],
        xlabel='月均净收益差（百分点）',xlim=(lower-span*.1,upper+span*.65),title='主设置显著性：87个完整月份，HAC(3)与95%置信区间')
    ax.axvline(0,color='grey',linewidth=1);ax.invert_yaxis();ax.grid(axis='x',alpha=.2)
    fig.text(.05,.025,'单项置信区间；Holm按四策略全部6组比较调整。检验月均收益差，不是直接检验CAGR；同样本探索性结果。',fontsize=10)
    fig.tight_layout(rect=(0,.07,1,1))
    fig.savefig(target/'03_significance.png',dpi=170)
    fig.savefig(target/'03_significance.svg')
    plt.close(fig)
    print('Saved three verified-source figure pairs')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path)
    main(parser.parse_args().target)
