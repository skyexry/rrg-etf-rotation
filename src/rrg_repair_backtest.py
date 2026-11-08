"""Replay four fixed strategies and audit retirements before final reporting."""
import argparse

import numpy as np
import pandas as pd

from rrg_pipeline import bind_upstream, verify_upstream, write_json
from rrg_report_tushare import START, END, sha, validate
from rrg_repair_inputs import DB, OUT
from rrg_repair_download import RAW


def settlement_prices(weights, close, open_, meta, *, save_plan_to=None):
    """User-approved NAV approximation, observable at each formation date."""
    old=pd.read_csv(OUT/'retirement_data_query_audit.csv')
    plans=[];close=close.copy();open_=open_.copy()
    for code in sorted(old.code.unique()):
        retired=meta.loc[code,'delist_date']
        dates=weights.index[(weights.index>=retired)&(weights.index<close.index[-1])]
        assert len(dates)>0,(code,'No settlement formation date in window')
        formation=dates[0];execution=close.index[close.index.get_loc(formation)+1]
        assert weights.loc[formation,code]==0
        nav=pd.read_parquet(RAW/f'fund_nav/{code}.parquet')
        nav['nav_date']=pd.to_datetime(nav.nav_date,format='%Y%m%d')
        nav['ann_date']=pd.to_datetime(nav.ann_date,format='%Y%m%d')
        nav=nav.loc[(nav.ann_date<=formation)&(nav.nav_date<=formation)&nav.unit_nav.gt(0)]
        assert not nav.empty,(code,'No already-announced NAV')
        value=nav.sort_values(['nav_date','ann_date']).iloc[-1]
        factors=pd.read_parquet(RAW/f'fund_adj/{code}.parquet')
        factors['date']=pd.to_datetime(factors.trade_date,format='%Y%m%d')
        factors=factors.loc[factors.date.le(value.nav_date)&factors.adj_factor.gt(0)]
        assert not factors.empty,(code,'No matching historical adjustment factor')
        factor=factors.sort_values('date').iloc[-1]
        price=float(value.unit_nav*factor.adj_factor)
        assert pd.isna(open_.loc[execution,code]),(code,'Real quote exists; do not replace it')
        close.loc[execution,code]=price;open_.loc[execution,code]=price
        plans.append({'code':code,'official_delist_date':str(retired.date()),
            'formation_date':str(formation.date()),'execution_date':str(execution.date()),
            'nav_date':str(value.nav_date.date()),'nav_ann_date':str(value.ann_date.date()),
            'unit_nav':float(value.unit_nav),'adjustment_factor':float(factor.adj_factor),
            'factor_date':str(factor.date.date()),'adjusted_settlement_price':price,
            'approximation':'NAV cash settlement estimate, not a market trade or verified cash payment.'})
    destination=OUT/'approved_nav_settlement_plan.csv' if save_plan_to is None else save_plan_to
    pd.DataFrame(plans).to_csv(destination,index=False)
    return close,open_,plans


def main(settle=False):
    bind_upstream(DB)
    from src.backtest import run_vbt_backtest, portfolio_stats
    from src.data_provider import fetch_etf_daily
    import vectorbt as vbt
    names=['Pure_RRG_Q1','Report_base','Industry_momentum_220_6','ETF_equal_weight']
    weights={n:pd.read_parquet(OUT/f'{n}_weights.parquet') for n in names}
    columns=sorted(set().union(*(set(w.columns) for w in weights.values())))
    close,open_,_=fetch_etf_daily(columns,START,END,lookback=25)
    close,open_=close[columns].loc[START:],open_[columns].loc[START:]
    meta=pd.read_parquet(OUT/'etf_metadata_audit.parquet').set_index('ts_code')
    plans=[]
    if settle:
        close,open_,plans=settlement_prices(weights['ETF_equal_weight'].reindex(columns=columns).fillna(0.),close,open_,meta)
    previous=vbt.settings.portfolio['call_seq'];vbt.settings.portfolio['call_seq']='auto'
    rows,checks,navs,retirements=[],{},{},[]
    try:
        for name,w in weights.items():
            w=w.reindex(columns=columns).fillna(0.)
            assert np.isfinite(w.to_numpy()).all() and w.min().min()>=-1e-8
            assert w.sum(axis=1).max()<=1+1e-5
            for code in columns:
                used=w.index[w[code]>1e-8]
                assert (used>=meta.loc[code,'list_date']).all()
                retired=meta.loc[code,'delist_date']
                if pd.notna(retired): assert (used<retired).all()
            pf=run_vbt_backtest(w,close,open_,fees=.0003,slippage=0.,init_cash=1e7)
            check=validate(pf,w,close,.0003)
            orders=pf.orders.records_readable
            signed=orders.Size.where(orders.Side.eq('Buy'),-orders.Size)
            dq=orders.assign(signed=signed).pivot_table(index='Timestamp',columns='Column',values='signed',aggfunc='sum').reindex(index=close.index,columns=columns).fillna(0.)
            shares=dq.cumsum()
            actual=(shares*close.ffill()).fillna(0.).div(pf.value(),axis=0)
            if settle:
                for plan in plans:
                    assert shares.loc[plan['execution_date']:,plan['code']].abs().max()<1e-5
                if name!='ETF_equal_weight':
                    prior=pd.read_csv(OUT/f'{name}_nav.csv',index_col=0).iloc[:,0]
                    np.testing.assert_allclose(pf.value().to_numpy(),prior.to_numpy(),rtol=1e-12)
                settled=orders.loc[orders.Column.isin([p['code'] for p in plans]) & orders.Side.eq('Sell')].copy()
                dates_by_code={p['code']:pd.Timestamp(p['execution_date']) for p in plans}
                settled=settled.loc[settled.Timestamp.eq(settled.Column.map(dates_by_code))]
                settled.to_csv(OUT/f'{name}_estimated_settlement_transactions.csv',index=False)
            for code in columns:
                if meta.loc[code,'list_status']!='D': continue
                last=close[code].last_valid_index()
                if last is None or last>=close.index[-1]: continue
                stale=shares.loc[shares.index>last,code]
                held=stale[stale>1e-5]
                if not held.empty:
                    retirements.append({'strategy':name,'code':code,'last_quote':str(last.date()),
                        'official_delist_date':str(meta.loc[code,'delist_date']),
                        'first_unpriced_holding_date':str(held.index[0].date()),
                        'first_unpriced_weight':float(actual.loc[held.index[0],code]),
                        'terminal_shares':float(shares.iloc[-1][code])})
            active=actual.sum(axis=1)>1e-8
            before=pf.value().shift(1).fillna(1e7)
            rows.append(dict(portfolio_stats(pf),strategy=name,**check,
                annualized_two_way_turnover=float((orders.Size*orders.Price/orders.Timestamp.map(before)).sum()*252/len(close)),
                mean_cash_fraction=float((pf.cash()/pf.value()).mean()),
                mean_largest_actual_etf_weight=float(actual.loc[active].max(axis=1).mean())))
            checks[name]=check;navs[name]=pf.value()/1e7
            pf.value().to_csv(OUT/f'{name}_nav.csv')
            orders.to_csv(OUT/f'{name}_orders.csv',index=False)
            actual.to_parquet(OUT/f'{name}_actual_weights.parquet')
            print(name,rows[-1]['年化收益率'],rows[-1]['最大回撤'],flush=True)
    finally:
        vbt.settings.portfolio['call_seq']=previous
    pd.DataFrame(rows).set_index('strategy').to_csv(OUT/('performance.csv' if settle else 'performance_before_retirement_review.csv'))
    navs=pd.DataFrame(navs);navs.to_csv(OUT/('net_nav.csv' if settle else 'net_nav_before_retirement_review.csv'))
    if not settle:
        pd.DataFrame(retirements).to_csv(OUT/'unpriced_retirement_positions.csv',index=False)
    write_json(OUT/('validation.json' if settle else 'execution_audit.json'),{'accounting':checks,'retirement_positions_needing_review':len(retirements),
        'input_sha256':sha(DB),'upstream_commit':verify_upstream(),
        'settings':{'fees':.0003,'slippage':0,'annualization':252,'execution':'next-open, sell-first, zero exits'},
        'approved_NAV_settlement_estimates':len(plans),'retired_positions_zero_after_settlement':settle,
        'status':'approved_nav_approximation_complete' if settle else ('review_retirement_positions' if retirements else 'ready_for_final_report')})
    if settle:
        assert len(retirements)==0
        monthly=navs.resample('ME').last().pct_change(fill_method=None).loc['2019-01-01':]
        assert monthly.shape==(87,4) and monthly.notna().all().all()
        monthly.to_csv(OUT/'monthly_returns_complete.csv')
        all_months=navs.resample('ME').last().pct_change(fill_method=None)
        all_months.iloc[0]=navs.resample('ME').last().iloc[0]-1
        ((1+all_months).groupby(all_months.index.year).prod()-1).to_csv(OUT/'annual_returns.csv')
        write_json(OUT/'run.json',{'period':[START,END],'params':[220,60,20,6],'fees':.0003,
            'slippage':0,'annualization':252,'initial_cash':1e7,'input_sha256':sha(DB),
            'upstream_commit':verify_upstream(),'primary_strategies':names,
            'old_data_retained':True,'membership_intervals_added':0,
            'pool':'Tushare industry/theme index categories, historical L/D candidates, matched OF aliases, listing/delist dates and observed formation-day quotes.',
            'user_approved_retirement_rule':'First monthly rebalance on/after delisting; cash settlement estimate at NAV already announced by formation; charge 3bp.',
            'cash_settlement_warning':'Modeled NAV settlement date/value, not verified actual liquidation cash flows.',
            'limitations':['Early membership coverage unchanged; full ETF holdings still incomplete.',
                'Historical changes in product/index category and ETF tracked-index identity unavailable.',
                'No claim of independent out-of-sample validation.']})
    print('Unpriced retirement holdings:',len(retirements),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settle',action='store_true',help='Use the user-approved observable NAV approximation.')
    main(parser.parse_args().settle)
