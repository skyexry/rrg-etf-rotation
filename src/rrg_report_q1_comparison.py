"""Add pure Q1 ETF implementation to the fixed report-period comparison."""
import json

import numpy as np
import pandas as pd

from rrg_pipeline import EXCLUDE,bind_upstream,verify_upstream,write_json
from rrg_report_tushare import OUT as SOURCE,DB,PARENT,START,END,sha,validate


def main():
    target=SOURCE/'pure_q1_comparison'
    target.mkdir(parents=True,exist_ok=True)
    run=json.loads((SOURCE/'run.json').read_text())
    assert sha(DB)==run['input_sha256']
    assert verify_upstream()==run['upstream_commit']
    bind_upstream(DB)
    from src.strategy import select_by_quadrant
    from src.optimizer import batch_optimize
    from src.data_provider import fetch_etf_meta,fetch_etf_daily
    from src.backtest import build_exposure_panel,mask_listed,run_vbt_backtest,portfolio_stats
    import vectorbt as vbt

    ratio=pd.read_parquet(PARENT/'rs_ratio.parquet')
    momentum=pd.read_parquet(PARENT/'rs_momentum.parquet')
    assert not set(EXCLUDE)&set(ratio.columns)
    reference_scores=pd.read_parquet(SOURCE/'industry_selection.parquet')
    dates=reference_scores.index
    assert ratio.loc[dates].notna().all().all() and momentum.loc[dates].notna().all().all()
    q1=select_by_quadrant(ratio,momentum,quadrants=(1,),top_n=6).loc[dates].astype(float)
    assert q1.sum(axis=1).le(6).all()
    # Same stored, point-in-time inputs as all three existing portfolios.
    panel=pd.read_parquet(SOURCE/'candidate_etf_panel.parquet')
    iw=pd.read_parquet(SOURCE/'visible_etf_industry_weights.parquet')
    exposures=build_exposure_panel(panel,iw)
    assert set(dates).issubset(exposures)
    meta=fetch_etf_meta(only_listed=True,drop_qdii=True)
    meta=meta.loc[meta.list_date.le(pd.Timestamp(END))]
    new_weights=mask_listed(batch_optimize(q1,exposures=exposures,dates=dates,preset='base',strict=True),meta)
    weights={'Pure_RRG_Q1':new_weights}
    for name in ['Report_base','Industry_momentum_220_6','ETF_equal_weight']:
        weights[name]=pd.read_parquet(SOURCE/f'{name}_weights.parquet')
    columns=sorted(set().union(*(set(w.columns) for w in weights.values())))
    close,open_,_=fetch_etf_daily(columns,START,END,lookback=25)
    close,open_=close[columns].loc[START:],open_[columns].loc[START:]
    q1.to_parquet(target/'Pure_RRG_Q1_signals.parquet')
    reference_scores.to_parquet(target/'combined_reference_signals.parquet')
    rows,checks,navs=[],{},{}
    old=vbt.settings.portfolio['call_seq']
    vbt.settings.portfolio['call_seq']='auto'
    try:
        for name,w in weights.items():
            print('Running '+name,flush=True)
            assert w.index.equals(dates)
            w=w.reindex(index=dates,columns=columns).fillna(0.)
            assert np.isfinite(w.to_numpy()).all() and w.min().min()>=-1e-8
            assert w.sum(axis=1).max()<=1+1e-5
            for code in w:
                assert (w.index[w[code]>1e-8]>=meta.loc[code,'list_date']).all()
            if name=='Pure_RRG_Q1':
                for d,E in exposures.items():
                    if d not in dates:
                        continue
                    k=int(q1.loc[d].sum())
                    if k:
                        selected=q1.columns[q1.loc[d].gt(0)]
                        exp=E.reindex(columns=selected,fill_value=0).T @ w.loc[d].reindex(E.index)
                        assert exp.max()<=1/k+2e-5
            pf=run_vbt_backtest(w,close,open_,fees=.0003,slippage=0.,init_cash=1e7)
            check=validate(pf,w,close,.0003)
            if name!='Pure_RRG_Q1':
                prior=pd.read_csv(SOURCE/f'{name}_nav.csv',index_col=0).iloc[:,0]
                np.testing.assert_allclose(pf.value().to_numpy(),prior.to_numpy(),rtol=1e-12)
                check['prior_nav_replayed_exactly']=True
            orders=pf.orders.records_readable
            signed=orders.Size.where(orders.Side.eq('Buy'),-orders.Size)
            dq=orders.assign(signed=signed).pivot_table(index='Timestamp',columns='Column',values='signed',aggfunc='sum').reindex(index=close.index,columns=columns).fillna(0.)
            shares=dq.cumsum()
            actual=(shares*close.ffill()).fillna(0.).div(pf.value(),axis=0)
            active=actual.sum(axis=1)>1e-8
            prior_nav=pf.value().shift(1).fillna(1e7)
            turnover=float((orders.Size*orders.Price/orders.Timestamp.map(prior_nav)).sum()*252/len(close))
            result=dict(portfolio_stats(pf),strategy=name,**check,
                annualized_two_way_turnover=turnover,mean_cash_fraction=float((pf.cash()/pf.value()).mean()),
                mean_largest_actual_etf_weight=float(actual.loc[active].max(axis=1).mean()),
                max_actual_etf_weight=float(actual.max().max()))
            rows.append(result);checks[name]=check;navs[name]=pf.value()/1e7
            w.to_parquet(target/f'{name}_weights.parquet')
            pf.value().to_csv(target/f'{name}_nav.csv')
            orders.to_csv(target/f'{name}_orders.csv',index=False)
    finally:
        vbt.settings.portfolio['call_seq']=old
    stats=pd.DataFrame(rows).set_index('strategy')
    stats.to_csv(target/'performance.csv')
    navs=pd.DataFrame(navs)
    navs.to_csv(target/'net_nav.csv')
    monthly=navs.resample('ME').last().pct_change(fill_method=None)
    monthly.iloc[0]=navs.resample('ME').last().iloc[0]-1
    annual=(1+monthly).groupby(monthly.index.year).prod()-1
    annual.to_csv(target/'annual_returns.csv')
    monthly.to_csv(target/'monthly_returns.csv')
    hashes={str(f):sha(f) for f in [PARENT/'rs_ratio.parquet',PARENT/'rs_momentum.parquet',
        SOURCE/'candidate_etf_panel.parquet',SOURCE/'visible_etf_industry_weights.parquet']}
    write_json(target/'run.json',dict(run,task='Pure Q1 ETF extension under identical report-period inputs',
        signal='First quadrant only, rank distance from center, at most six industries; no diffusion input.',
        implementation='Reuse unmodified upstream methods; this is not a from-scratch implementation.',
        portfolio_rule='Same base optimizer for Q1, combined and industry momentum; cap selected industry exposure at 1/k. k depends on signal.',
        source_hashes=hashes,comparison_strategies=list(weights),upstream_commit=verify_upstream()))
    write_json(target/'validation.json',dict(accounting=checks,three_existing_navs_replayed_exactly=True,
        q1_target_industry_caps_checked=True,listing_dates_checked=True,
        input_database_sha256=run['input_sha256'],source_hashes=hashes))
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from pathlib import Path
    font=Path('/Library/Fonts/Arial Unicode.ttf')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus']=False
    labels={'Pure_RRG_Q1':'纯 RRG 第一象限 ETF','Report_base':'扩散＋RRG ETF',
        'Industry_momentum_220_6':'行业动量 ETF','ETF_equal_weight':'ETF 等权'}
    colors=['#205F91','#9468A0','#D07E36','#628E7C']
    fig,axes=plt.subplots(2,1,figsize=(11,8),sharex=True,gridspec_kw={'height_ratios':[1.6,1]})
    for (name,label),color in zip(labels.items(),colors):
        n=navs[name]
        axes[0].plot(n.index,n,label=f'{label}：年化 {stats.loc[name,"年化收益率"]:.2%}',color=color,linewidth=1.6)
        axes[1].plot(n.index,(n/n.cummax()-1)*100,color=color,linewidth=1.3)
    axes[0].legend(frameon=False,fontsize=10)
    axes[0].set(ylabel='净值（初始=1）',title='相同数据、相同实施规则：纯第一象限是否值得单独采用？')
    axes[1].set(ylabel='回撤（%）')
    for ax in axes:
        ax.grid(alpha=.2)
    fig.text(.08,.025,'2018-12-28—2026-03-31｜月度调仓、先卖后买、买卖各3bp｜数据口径与覆盖差异保留于报告',fontsize=10)
    fig.tight_layout(rect=(0,.055,1,1))
    for suffix in ['png','svg']:
        fig.savefig(target/f'comparison.{suffix}',dpi=170)
    print(stats.to_string(),flush=True)


if __name__=='__main__':
    main()
