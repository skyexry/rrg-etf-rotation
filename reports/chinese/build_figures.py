"""Print-size vector charts from the frozen exported experiment tables."""
from pathlib import Path
import html
import math

import pandas as pd

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parents[1]/'results/rrg_tushare_repaired_20261107'
ROB=SOURCE/'four_strategy_robustness'
FILT=SOURCE/'sma10_filter'
OUT=HERE/'figures'
NAMES=['Pure_RRG_Q1','Report_base','Industry_momentum_220_6','ETF_equal_weight']
LABELS=['纯第一象限','扩散＋RRG','行业动量','ETF等权']
COLORS=['#205F91','#9468A0','#D07E36','#628E7C']
W=816


def text(x,y,s,size=16,anchor='start',color='#111',weight='normal'):
    return f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" fill="{color}" font-weight="{weight}">{html.escape(str(s))}</text>'


def line(x1,y1,x2,y2,color='#D9DDE2',width=1):
    return f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width}"/>'


def save(name,parts,height):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 0 {W} {height}"><rect width="100%" height="100%" fill="white"/><g font-family="Arial Unicode MS, sans-serif">'+''.join(parts)+'</g></svg>'
    (OUT/f'{name}.svg').write_text(svg)


def legend(parts,labels=LABELS,colors=COLORS,dashed=None,top=24):
    for i,(label,color) in enumerate(zip(labels,colors)):
        x=75+(i%2)*375;y=top+(i//2)*24
        dash=' stroke-dasharray="7 4"' if dashed and dashed[i] else ''
        parts.append(f'<line x1="{x}" y1="{y-5}" x2="{x+29}" y2="{y-5}" stroke="{color}" stroke-width="2.5"{dash}/>')
        parts.append(text(x+38,y,label,16))


def timeseries(data,name,dual=False,labels=LABELS,colors=COLORS,gate=None):
    parts=[];legend(parts,labels,colors,[False]*4 if gate is None else [True,False,True,False])
    height=590 if dual else 355
    panels=[(data,80,285 if dual else 220,'净值' if dual else '滚动12个月收益 %')]
    if dual:panels.append(((data/data.cummax()-1)*100,405,130,'回撤 %'))
    ts=(data.index-data.index[0]).total_seconds().to_numpy()
    span=ts[-1];x=lambda t:64+t/span*728
    for vals,top,h,ylabel in panels:
        low=float(vals.min().min());high=float(vals.max().max())
        if dual and top==80: low=min(1.,low);high=math.ceil(high)
        else:low=math.floor(low/10)*10;high=max(0,math.ceil(high/10)*10)
        if high==low:high=low+1
        y=lambda v:top+h-(v-low)/(high-low)*h
        if gate is not None:
            off=gate.reindex(data.index).eq(0).to_numpy()
            start=None
            for i,flag in enumerate([*off,False]):
                if flag and start is None:start=i
                if not flag and start is not None:
                    end=min(i,len(ts)-1)
                    parts.append(f'<rect x="{x(ts[start]):.2f}" y="{top}" width="{max(1,x(ts[end])-x(ts[start])):.2f}" height="{h}" fill="#EFEFEF"/>')
                    start=None
        step=1 if dual and top==80 else 20 if high-low>60 else 10
        for v in range(math.ceil(low/step)*step,int(high)+1,step):
            parts.extend([line(64,y(v),792,y(v)),text(54,y(v)+5,f'{v:g}',14,'end')])
        parts.append(text(64,top-12,ylabel,16))
        for year in range(2019,2027):
            tick=pd.Timestamp(f'{year}-01-01')
            if data.index[0]<=tick<=data.index[-1]:
                xx=x((tick-data.index[0]).total_seconds())
                parts.append(line(xx,top,xx,top+h,'#EDF0F3'))
                if not dual or top!=80:
                    parts.append(text(xx,top+h+24,year,14,'middle'))
        for i,col in enumerate(vals):
            pts=' '.join(f'{x(t):.1f},{y(v):.1f}' for t,v in zip(ts,vals[col]))
            dash=' stroke-dasharray="7 4"' if gate is not None and i in [0,2] else ''
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{colors[i]}" stroke-width="2.1"{dash}/>')
    save(name,parts,height)


def main():
    OUT.mkdir(exist_ok=True)
    nav=pd.read_csv(SOURCE/'net_nav.csv',index_col=0,parse_dates=True)[NAMES]
    timeseries(nav,'01_net_nav',dual=True)
    rolling=pd.read_csv(ROB/'rolling_12m_returns.csv',index_col=0,parse_dates=True)[NAMES]*100
    timeseries(rolling,'06_rolling_returns')
    cost=pd.read_csv(ROB/'cost_cagr.csv',index_col=0)[NAMES]*100
    parts=[];legend(parts)
    x=lambda v:70+(v-3)/17*640;y=lambda v:300-(v-8)/18*220
    for v in [10,15,20,25]:parts.extend([line(70,y(v),714,y(v)),text(58,y(v)+5,v,14,'end')])
    for bp in cost.index:parts.append(text(x(bp),326,bp,16,'middle'))
    for i,n in enumerate(NAMES):
        pts=' '.join(f'{x(bp):.2f},{y(v):.2f}' for bp,v in cost[n].items())
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{COLORS[i]}" stroke-width="2.7"/>')
        for bp,v in cost[n].items():parts.append(f'<circle cx="{x(bp):.2f}" cy="{y(v):.2f}" r="4" fill="{COLORS[i]}"/>')
        parts.append(text(725,y(cost.loc[20,n])+5,f'{cost.loc[20,n]:.2f}%',15,color=COLORS[i]))
    parts.extend([text(70,72,'扣费年化 %'),text(408,354,'单边总交易摩擦 bp',16,'middle')])
    save('02_cost',parts,370)
    grid=pd.read_csv(ROB/'parameter_grid.csv')
    parts=[]
    for z,n in enumerate(NAMES[:3]):
        mat=grid[grid.strategy.eq(n)].pivot(index='lookback',columns='max_industries',values='年化收益率')*100
        left=56+z*254;top=45;cw=75;ch=65
        parts.append(text(left+112,25,LABELS[z],18,'middle'))
        for i,L in enumerate(mat.index):
            for j,k in enumerate(mat.columns):
                val=mat.loc[L,k];a=(val-12)/13;a=max(0,min(1,a))
                rgb=tuple(round(s+(e-s)*a) for s,e in zip((240,246,251),(32,95,145)))
                color='#%02x%02x%02x'%rgb
                parts.append(f'<rect x="{left+j*cw}" y="{top+i*ch}" width="{cw}" height="{ch}" fill="{color}"/>')
                parts.append(text(left+(j+.5)*cw,top+(i+.5)*ch+6,f'{val:.2f}',18,'middle',color='white' if a>.62 else '#111'))
            if z==0:parts.append(text(left-9,top+(i+.5)*ch+5,L,15,'end'))
        for j,k in enumerate(mat.columns):parts.append(text(left+(j+.5)*cw,264,k,16,'middle'))
        parts.append(f'<rect x="{left+cw}" y="{top+ch}" width="{cw}" height="{ch}" fill="none" stroke="black" stroke-width="3"/>')
        parts.append(text(left+112,290,'最多行业数',15,'middle'))
    parts.append(text(56,320,'单元格为扣费年化 %   行为180 220 260日   黑框为主设置',15))
    save('03_parameters',parts,340)
    tests=pd.read_csv(ROB/'primary_pairwise_tests.csv')
    parts=[];lo=-1.1;hi=2.1;x=lambda v:190+(v-lo)/(hi-lo)*390
    parts.append(line(x(0),30,x(0),342,'#888',1.4))
    mapping=dict(zip(NAMES,LABELS))
    for i,row in tests.iterrows():
        a,b=row.comparison.split(' minus ');yy=48+i*53
        parts.append(text(181,yy+5,mapping[a]+'－'+mapping[b],15,'end'))
        low,high,mu=[100*row[k] for k in ['ci95_low','ci95_high','mean_monthly_difference']]
        parts.extend([line(x(low),yy,x(high),yy,'#205F91',2.5),line(x(low),yy-6,x(low),yy+6,'#205F91',2),line(x(high),yy-6,x(high),yy+6,'#205F91',2)])
        parts.append(f'<circle cx="{x(mu):.2f}" cy="{yy}" r="4.5" fill="#205F91"/>')
        parts.append(text(594,yy+5,f'p {row.p_two_sided:.3f}  Holm {row.p_Holm_6:.3f}',15))
    for v in [-1,0,1,2]:parts.extend([line(x(v),337,x(v),343,'#555'),text(x(v),366,v,15,'middle')])
    parts.append(text(390,395,'月均净收益差 百分点',16,'middle'))
    save('04_significance',parts,410)
    annual=pd.read_csv(ROB/'annual_returns.csv',index_col=0).loc[2019:,NAMES]*100
    parts=[];legend(parts)
    y=lambda v:300-(v+25)/125*215
    for v in [-20,0,20,40,60,80,100]:parts.extend([line(64,y(v),792,y(v)),text(55,y(v)+5,v,14,'end')])
    for k,(year,row) in enumerate(annual.iterrows()):
        left=74+k*90
        for i,n in enumerate(NAMES):
            v=row[n];yy=min(y(v),y(0));h=abs(y(v)-y(0))
            parts.append(f'<rect x="{left+i*17}" y="{yy:.2f}" width="15" height="{h:.2f}" fill="{COLORS[i]}"/>')
        parts.append(text(left+31,329,'2026Q1' if year==2026 else year,14,'middle'))
    parts.append(text(64,72,'区间收益 %'))
    save('05_annual_returns',parts,350)
    fnav=pd.read_csv(FILT/'net_nav.csv',index_col=0,parse_dates=True)
    gate=pd.read_csv(FILT/'effective_daily_market_filter.csv',index_col=0,parse_dates=True).iloc[:,0]
    timeseries(fnav,'07_sma10',dual=True,
        labels=['纯第一象限 未过滤','纯第一象限 加均线','动量 未过滤','动量 加均线'],
        colors=[COLORS[0],COLORS[0],COLORS[2],COLORS[2]],gate=gate)
    print('Seven print-size SVG charts generated.')


if __name__=='__main__':main()
