"""Presentation figures from existing verified results; no strategy reruns."""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd


def main():
    root=Path(__file__).resolve().parents[1]
    base=root/'results/rrg/author_baseline_comparison'
    target=base/'ablation/q1_presentation_evidence'
    target.mkdir(parents=True,exist_ok=True)
    font=Path('/Library/Fonts/Arial Unicode.ttf')
    if not font.exists():
        font=Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
        'font.size':12,'axes.titlesize':15,'axes.labelsize':12,
        'axes.spines.top':False,'axes.spines.right':False,
        'axes.unicode_minus':False,'svg.fonttype':'path'})
    labels={'RRG_Q1_only':'纯 RRG 第一象限（220日／最多6行业）',
        'Industry_momentum_220_6':'行业动量（220日／6行业）','ETF_equal_weight':'ETF 等权'}
    colors={'RRG_Q1_only':'#1D6390','Industry_momentum_220_6':'#D27B32','ETF_equal_weight':'#648E7F'}
    names=list(labels)
    navs={}
    for name in names:
        f=base/'ablation/RRG_Q1_only_nav.csv' if name=='RRG_Q1_only' else base/f'{name}_sell_first_nav.csv'
        navs[name]=pd.read_csv(f,index_col=0,parse_dates=True).iloc[:,0]/1e7
    nav=pd.DataFrame(navs)
    cagr=(nav.iloc[-1]**(252/len(nav))-1)*100
    dd=nav.div(nav.cummax()).sub(1)*100
    assert abs(cagr.RRG_Q1_only-15.525048245367)<1e-8
    fig,axes=plt.subplots(2,1,figsize=(12,8),sharex=True,gridspec_kw={'height_ratios':[1.6,1]})
    for name in names:
        axes[0].plot(nav.index,nav[name],color=colors[name],linewidth=2,
            label=f'{labels[name]}：年化 {cagr[name]:.2f}%')
        axes[1].plot(dd.index,dd[name],color=colors[name],linewidth=1.6)
    axes[0].set(ylabel='净值（初始 = 1）',title='收益更多，但仍需承受约 30% 的最大回撤')
    axes[0].legend(loc='upper left',fontsize=10,frameon=False)
    axes[1].set(ylabel='相对前期高点回撤（%）')
    for name in names:
        i=dd[name].idxmin()
        axes[1].scatter(i,dd.at[i,name],s=22,color=colors[name],zorder=4)
    axes[1].text(.02,.08,'最大回撤：第一象限 30.16%｜行业动量 31.28%｜ETF 等权 27.80%',
        transform=axes[1].transAxes,fontsize=10,bbox={'facecolor':'white','alpha':.9,'edgecolor':'none'})
    for ax in axes:
        ax.grid(alpha=.18)
    fig.suptitle('主动轮动多赚的钱，承担了什么代价？',fontsize=19,y=.98)
    fig.text(.07,.035,'2022-09—2026-08｜同一作者快照与 ETF 候选池｜月度调仓、先卖后买｜买卖各 3 bp，额外滑点 0',fontsize=10)
    fig.text(.07,.012,'注：历史样本内比较；原设置相对两基准的月均净收益差均未达 5% 显著性。',fontsize=10,color='#555555')
    fig.tight_layout(rect=(.02,.065,.99,.95))
    for suffix in ['png','svg']:
        fig.savefig(target/f'01_return_and_drawdown.{suffix}',dpi=180)
    plt.close(fig)

    cost=pd.read_csv(base/'ablation/time_cost_checks/cost_cagr_all_five.csv',index_col=0)
    grid=pd.read_csv(base/'ablation/q1_parameter_checks/parameter_grid.csv')
    dependency=pd.read_csv(base/'ablation/time_cost_checks/month_dependency_all_five.csv').set_index('strategy')
    tests=pd.read_csv(base/'ablation/q1_mechanism_significance/paired_HAC_tests.csv').query('HAC_lags==3')
    fig,axes=plt.subplots(2,2,figsize=(14,10))
    ax=axes[0,0]
    for name in names:
        ax.plot(cost.index,cost[name]*100,marker='o',color=colors[name],label=labels[name].split('（')[0],linewidth=2)
        ax.text(20.4,cost.loc[20,name]*100,f'{cost.loc[20,name]*100:.2f}%',color=colors[name],va='center',fontsize=10)
    ax.set(title='① 成本提高后，收益仍领先',xlabel='单边总交易摩擦（bp）',ylabel='扣费年化收益（%）',xticks=[3,5,10,20],xlim=(2,23))
    ax.legend(frameon=False,fontsize=10,loc='upper right')
    ax.grid(alpha=.18)

    ax=axes[0,1]
    values=grid.pivot(index='lookback',columns='max_industries',values='年化收益率')*100
    face=np.zeros((3,3,3))
    for i in range(3):
        for j in range(3):
            v=values.iloc[i,j]
            face[i,j]=matplotlib.colors.to_rgb('#DCECE4' if v>12.152427 else '#FFF0D4' if v>10.28429 else '#EEF0F2')
    ax.imshow(face,aspect='auto')
    for i in range(3):
        for j in range(3):
            text=f'{values.iloc[i,j]:.2f}%'+('\n原设置' if (i,j)==(1,1) else '')
            ax.text(j,i,text,ha='center',va='center',fontsize=12)
    ax.add_patch(Rectangle((.5,.5),1,1,fill=False,edgecolor=colors['RRG_Q1_only'],linewidth=2))
    ax.set(title='② 5／9 组同时超过两个固定基准',xlabel='最多配置的行业数',ylabel='长期回看期（交易日）',
        xticks=range(3),xticklabels=values.columns,yticks=range(3),yticklabels=values.index)
    ax.text(.0,-.27,'绿色：同时领先｜黄色：仅领先等权｜灰色：均未领先',transform=ax.transAxes,fontsize=9,color='#555555')

    ax=axes[1,0]
    x=np.arange(3);width=.34
    actual=[cagr[n] for n in names]
    stressed=[dependency.loc[n,'best_3_months_flat_cagr']*100 for n in names]
    ax.bar(x-width/2,actual,width,color='#1D6390',label='实际年化')
    ax.bar(x+width/2,stressed,width,color='#A7BBC9',label='最佳3个月收益置零')
    for xs,ys in [(x-width/2,actual),(x+width/2,stressed)]:
        for xx,yy in zip(xs,ys):
            ax.text(xx,yy+(.45 if yy>=0 else -.55),f'{yy:.2f}%',ha='center',va='bottom' if yy>=0 else 'top',fontsize=10)
    ax.axhline(0,color='grey',linewidth=.8)
    ax.set(title='③ 收益依赖少数上涨月，基准也如此',ylabel='年化收益（%）',xticks=x,xticklabels=['第一象限','行业动量','ETF 等权'],ylim=(-4,19))
    ax.legend(frameon=False,fontsize=10,loc='upper right')
    ax.grid(axis='y',alpha=.18)
    ax.text(.0,-.23,'事后算术诊断：保留全部日期，仅将所选月份收益设为0；非新策略回测。',transform=ax.transAxes,fontsize=9,color='#555555')

    ax=axes[1,1]
    rows=[('相对行业动量','Q1_220_6 minus Industry_momentum_220_6'),('相对 ETF 等权','Q1_220_6 minus ETF_equal_weight')]
    for i,(label,key) in enumerate(rows):
        r=tests.loc[tests.comparison.eq(key)].iloc[0]
        mu,lo,hi=[r[k]*100 for k in ['mean_monthly_difference','ci95_low','ci95_high']]
        ax.errorbar(mu,i,xerr=np.array([[mu-lo],[hi-mu]]),fmt='o',capsize=6,color=colors['RRG_Q1_only'])
        ax.text(1.55,i,f'p={r.p_two_sided:.3f}',va='center',fontsize=11)
    ax.axvline(0,color='grey',linewidth=1)
    ax.set(title='④ 观察到领先，统计持续性待确认',xlabel='月均净收益差（百分点）；线段为95%置信区间',
        yticks=range(2),yticklabels=[r[0] for r in rows],xlim=(-.7,2.0),ylim=(-.6,1.6))
    ax.invert_yaxis();ax.grid(axis='x',alpha=.18)
    ax.text(.0,-.23,'48个不重叠月份；Newey–West/HAC(3)；原220日／6行业设置。',transform=ax.transAxes,fontsize=9,color='#555555')
    fig.suptitle('多赚的钱可靠吗？四项证据一起看',fontsize=21,y=.985)
    fig.text(.055,.025,'历史窗口：2022-09—2026-08。成本情景将总摩擦统一作为费率扣除；参数网格为同样本敏感性分析。',fontsize=10,color='#555555')
    fig.tight_layout(rect=(.02,.07,.99,.95),h_pad=3.5,w_pad=2.5)
    for suffix in ['png','svg']:
        fig.savefig(target/f'02_robustness_evidence.{suffix}',dpi=180)
    plt.close(fig)
    print(target)


if __name__=='__main__':
    main()
