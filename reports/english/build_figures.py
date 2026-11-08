"""Reproduce the seven English figures from archived, fixed-sample results."""
from pathlib import Path
import os
os.environ.setdefault('MPLCONFIGDIR', '/private/tmp/rrg_publication_mpl')
import hashlib
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager, colors
from matplotlib.patches import Rectangle
from matplotlib.ticker import MultipleLocator
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SRC=HERE/'data'
OUT=HERE/'figures'
TABLES=HERE/'tables'
NAMES=['Pure_RRG_Q1','Report_base','Industry_momentum_220_6','ETF_equal_weight']
LABELS=['Pure RRG','Diffusion + RRG','Momentum','Equal weight']
COLORS=['#173F5F','#82618C','#BD7F2A','#3D8076']
STYLES=['-','--','-.',':']
for name in ['Times New Roman.ttf','Times New Roman Bold.ttf','Times New Roman Italic.ttf','Times New Roman Bold Italic.ttf']:
    f=Path('/System/Library/Fonts/Supplemental')/name
    if f.exists():font_manager.fontManager.addfont(f)
plt.rcParams.update({
    'font.family':'serif','font.serif':['Times New Roman','DejaVu Serif'],
    'font.size':9,'axes.labelsize':9,'axes.titlesize':9.5,
    'xtick.labelsize':8.5,'ytick.labelsize':8.5,'legend.fontsize':8.8,
    'mathtext.fontset':'stix','pdf.fonttype':42,'ps.fonttype':42,
    'axes.linewidth':.55,'axes.edgecolor':'#333333','text.color':'#181818',
    'axes.labelcolor':'#181818','xtick.color':'#333333','ytick.color':'#333333',
    'xtick.major.size':3,'ytick.major.size':3,'xtick.major.width':.5,
    'ytick.major.width':.5,'savefig.facecolor':'white','figure.facecolor':'white',
    'axes.spines.top':False,'axes.spines.right':False,
    'legend.frameon':False,'lines.linewidth':1.2,
    'savefig.dpi':300,'axes.unicode_minus':False})
inputs={}

def read(path,**kwargs):
    path=Path(path)
    inputs[str(path.relative_to(HERE))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return pd.read_csv(path,**kwargs)

def clean(ax,zero=False):
    ax.grid(axis='y',color='#E3E5E7',linewidth=.45)
    ax.set_axisbelow(True)
    if zero:ax.axhline(0,color='#858585',linewidth=.55,zorder=1)
    ax.tick_params(pad=3)

def save(fig,name):
    for ext in ['pdf','png']:
        fig.savefig(OUT/f'{name}.{ext}',bbox_inches='tight',pad_inches=.045,
                    metadata={'Creator':'Matplotlib; fixed archived RRG experiment'})
    plt.close(fig)

def legend(fig,handles,labels=LABELS,y=1.005):
    fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.52,y),
               ncol=4,handlelength=2.4,handletextpad=.5,columnspacing=1.2)

def time_axes(ax,index):
    ax.set_xlim(index[0],index[-1])
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))

def nav_plot(nav,name,labels=LABELS,gate=None):
    fig,axs=plt.subplots(2,1,figsize=(6.9,3.9),sharex=True,
        gridspec_kw={'height_ratios':[2.15,1],'hspace':.23})
    fig.subplots_adjust(left=.10,right=.985,bottom=.10,top=.87)
    dd=(nav/nav.cummax()-1)*100
    handles=[]
    palette=COLORS if gate is None else [COLORS[0],COLORS[0],COLORS[2],COLORS[2]]
    styles=STYLES if gate is None else ['-','--','-','--']
    if gate is not None:
        flags=gate.reindex(nav.index).eq(0).to_numpy()
        start=None
        for j,flag in enumerate([*flags,False]):
            if flag and start is None:start=j
            if not flag and start is not None:
                end=min(j,len(nav.index)-1)
                for ax in axs:ax.axvspan(nav.index[start],nav.index[end],color='#DADDE0',alpha=.5,lw=0,zorder=0)
                start=None
    for j,col in enumerate(nav):
        kw=dict(color=palette[j],linestyle=styles[j],lw=1.25 if j==0 else 1.05)
        h,=axs[0].plot(nav.index,nav[col],**kw);handles.append(h)
        axs[1].plot(nav.index,dd[col],**kw)
    axs[0].set_ylabel('Wealth (initial value = 1)')
    axs[1].set_ylabel('Drawdown (%)')
    axs[0].set_title('(a) Cumulative net performance',loc='left',pad=5)
    axs[1].set_title('(b) Peak-to-trough losses',loc='left',pad=5)
    axs[0].set_ylim(.7,max(5.5,float(nav.max().max())*1.04))
    axs[0].yaxis.set_major_locator(MultipleLocator(1))
    axs[1].set_ylim(-60 if dd.min().min()<-50 else -50,2)
    axs[1].yaxis.set_major_locator(MultipleLocator(20))
    for ax in axs:clean(ax);time_axes(ax,nav.index)
    axs[1].set_xlabel('Year',labelpad=3)
    legend(fig,handles,labels,y=1.0)
    save(fig,name)

OUT.mkdir(exist_ok=True)
nav=read(SRC/'net_nav.csv',index_col=0,parse_dates=True)[NAMES]
nav_plot(nav,'01_net_nav')

cost=read(TABLES/'cost_sensitivity.csv')
fig,ax=plt.subplots(figsize=(6.9,2.65))
fig.subplots_adjust(left=.10,right=.91,bottom=.20,top=.83)
handles=[]
for j,n in enumerate(NAMES):
    r=cost[cost.strategy.eq(n)].sort_values('one_way_bp')
    h,=ax.plot(r.one_way_bp,r['年化收益率']*100,color=COLORS[j],ls=STYLES[j],marker=['o','s','^','D'][j],ms=3.5,lw=1.25)
    handles.append(h)
    ax.annotate(f"{r.iloc[-1]['年化收益率']*100:.2f}",(20,r.iloc[-1]['年化收益率']*100),xytext=(6,0),textcoords='offset points',va='center',fontsize=8.5,color=COLORS[j])
ax.set(xlabel='One-way transaction cost (basis points)',ylabel='Annualized net return (%)',xlim=(2.5,21.2),ylim=(8,26))
ax.set_xticks([3,5,10,20]);ax.yaxis.set_major_locator(MultipleLocator(5));clean(ax)
legend(fig,handles,y=1.0);save(fig,'02_cost')

grid=read(TABLES/'parameter_grid.csv')
fig,axs=plt.subplots(1,3,figsize=(6.9,2.7))
fig.subplots_adjust(left=.085,right=.88,bottom=.22,top=.86,wspace=.17)
cmap=colors.LinearSegmentedColormap.from_list('rrg_blue',['#F4F6F8','#C9D9E6','#759BB7','#173F5F'])
norm=colors.Normalize(vmin=12,vmax=25)
for j,n in enumerate(NAMES[:3]):
    mat=grid[grid.strategy.eq(n)].pivot(index='lookback',columns='max_industries',values='年化收益率').sort_index()*100
    im=axs[j].pcolormesh(np.arange(4)-.5,np.arange(4)-.5,mat.to_numpy(),cmap=cmap,norm=norm,shading='flat',rasterized=False)
    axs[j].set_xlim(-.5,2.5);axs[j].set_ylim(2.5,-.5)
    axs[j].set_title(f'({chr(97+j)}) {LABELS[j]}',pad=8)
    axs[j].set_xticks([0,1,2],labels=['4','6','8'])
    axs[j].set_yticks([0,1,2],labels=['180','220','260'] if j==0 else [])
    axs[j].set_xlabel('Maximum sectors',labelpad=5)
    for a in range(3):
        for b in range(3):
            v=mat.iloc[a,b]
            axs[j].text(b,a,f'{v:.2f}',ha='center',va='center',fontsize=9,color='white' if v>20.8 else '#171717')
    axs[j].add_patch(Rectangle((.5,.5),1,1,fill=False,edgecolor='#111111',linewidth=1.3))
    axs[j].tick_params(length=0)
    for spine in axs[j].spines.values():spine.set_visible(False)
axs[0].set_ylabel('Lookback (trading days)',labelpad=6)
cax=fig.add_axes([.91,.22,.018,.64]);cb=fig.colorbar(im,cax=cax)
cb.set_label('Annualized net return (%)',labelpad=7);cb.set_ticks([12,16,20,24]);cb.outline.set_linewidth(.5)
cb.solids.set_rasterized(False)
save(fig,'03_parameters')

tests=read(TABLES/'primary_pairwise_tests.csv')
code=dict(zip(NAMES,['Q','C','M','E']))
fig=plt.figure(figsize=(6.9,3.15));ax=fig.add_axes([.105,.18,.57,.71])
y=np.arange(6)[::-1]
means=tests.mean_monthly_difference.to_numpy()*100
low=tests.ci95_low.to_numpy()*100;high=tests.ci95_high.to_numpy()*100
ax.errorbar(means,y,xerr=[means-low,high-means],fmt='o',color=COLORS[0],ms=4,capsize=3,elinewidth=1,capthick=.8)
ax.set_yticks(y,labels=[code[a]+' - '+code[b] for a,b in (s.split(' minus ') for s in tests.comparison)])
ax.set(xlim=(-1.05,2.1),ylim=(-.55,5.65),xlabel='Mean monthly net return difference (pp)')
ax.set_xticks([-1,0,1,2]);ax.axvline(0,color='#777777',lw=.75,ls='--');ax.grid(axis='x',color='#E3E5E7',lw=.45)
ax.spines['left'].set_visible(False);ax.tick_params(axis='y',length=0)
for x,label in [(.775,r'Raw $p$'),(.92,r'Holm $p$')]:fig.text(x,.94,label,ha='center',va='center',fontsize=9)
for j,r in tests.iterrows():
    yf=fig.transFigure.inverted().transform(ax.transData.transform((0,y[j])))[1]
    fig.text(.775,yf,f'{r.p_two_sided:.3f}',ha='center',va='center',fontsize=9)
    fig.text(.92,yf,f'{r.p_Holm_6:.3f}',ha='center',va='center',fontsize=9)
save(fig,'04_significance')

annual=read(TABLES/'annual_returns.csv',index_col=0)
annual.index=annual.index.astype(str)
annual=annual.loc[~annual.index.str.startswith('2018'),NAMES]*100
fig,ax=plt.subplots(figsize=(6.9,2.6));fig.subplots_adjust(left=.10,right=.985,bottom=.20,top=.83)
x=np.arange(len(annual));w=.18;handles=[]
for j,n in enumerate(NAMES):
    h=ax.bar(x+(j-1.5)*w,annual[n],width=w*.92,color=COLORS[j],edgecolor='white',lw=.2,hatch=['','///','','...'][j]);handles.append(h)
ax.set_xticks(x,labels=[s[:4] if not s.startswith('2026') else '2026 Q1' for s in annual.index])
ax.set(xlabel='Calendar period',ylabel='Net return (%)',ylim=(-28,103))
ax.yaxis.set_major_locator(MultipleLocator(20));clean(ax,zero=True);legend(fig,handles,y=1.0);save(fig,'05_annual_returns')

rolling=read(TABLES/'rolling_12m_returns.csv',index_col=0,parse_dates=True)[NAMES]*100
fig,ax=plt.subplots(figsize=(6.9,2.65));fig.subplots_adjust(left=.10,right=.985,bottom=.20,top=.83)
handles=[]
for j,n in enumerate(NAMES):
    h,=ax.plot(rolling.index,rolling[n],color=COLORS[j],ls=STYLES[j],lw=1.3 if j==0 else 1.05);handles.append(h)
ax.set(xlabel='Window end',ylabel='Trailing 12-month return (%)',ylim=(-30,102));ax.yaxis.set_major_locator(MultipleLocator(20));clean(ax,zero=True);time_axes(ax,rolling.index)
legend(fig,handles,y=1.0);save(fig,'06_rolling_returns')

fnav=read(SRC/'sma10_filter/net_nav.csv',index_col=0,parse_dates=True)
gate=read(SRC/'sma10_filter/effective_daily_market_filter.csv',index_col=0,parse_dates=True).iloc[:,0]
nav_plot(fnav,'07_sma10',labels=['Pure RRG','Pure RRG + SMA','Momentum','Momentum + SMA'],gate=gate)

for f in OUT.glob('*.pdf'):
    assert f.stat().st_size>3000
(HERE/'qa/figure_input_hashes.json').write_text(json.dumps(inputs,indent=2))
print('Seven English figures exported as vector PDF and 300-dpi PNG.')
