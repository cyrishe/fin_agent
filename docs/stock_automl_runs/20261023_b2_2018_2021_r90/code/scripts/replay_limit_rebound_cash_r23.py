"""R23B: frozen signals, CNY 1m; half cash when flat, all cash when carrying a lot."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path('outputs/stock_automl/limit_rebound_cash_rotation_r23b_20261015')
SOURCE = Path('outputs/stock_automl/limit_rebound_body8_nofallback_r21b_20261015')
BASE = Path('outputs/stock_automl/limit_rebound_segments_r18_20261015')
INITIAL = 1_000_000.


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def drawdown(values):
    a = np.asarray(values, float)
    return float(np.max(1-a/np.maximum.accumulate(a)))


def sharpe(returns):
    r = np.asarray(returns, float)
    sd = np.std(r, ddof=1) if len(r)>1 else 0.
    return float(np.sqrt(252)*np.mean(r)/sd) if sd>0 else None


def replay(frame, calendar, start='2026-01-01', end=None, initial=INITIAL, fee_rate=0.):
    assert 0<=fee_rate<1
    end = end or calendar[-1]
    dates = [d for d in calendar if start<=d<=end]
    index = {d:i for i,d in enumerate(calendar)}
    signals = frame[frame.d1_date.between(start,end)].sort_values('d1_date')
    assert not signals.d1_date.duplicated().any()
    entries = {}; excluded = []
    for row in signals.to_dict('records'):
        day = row['d1_date']; i = index[day]
        last = min(2,index[dates[-1]]-i+1)
        reason = None
        if not np.isfinite(row['entry']) or row['entry']<=0:
            reason = '买价缺失'
        elif not np.isfinite(row['d1_preclose']) or row['d1_preclose']<=0:
            reason = '买日前参考价无效'
        for k in range(1,last+1):
            price = row[f'd{k}_close']
            if not np.isfinite(price) or price<=0:
                reason = '持仓收价缺失'; break
            if k==2:
                dividend=float(row.get('d2_cash_dividend',0.) or 0.)
                if not np.isfinite(dividend) or dividend<0 or not np.isfinite(row['d2_preclose']+dividend-row['d1_close']) or abs(row['d2_preclose']+dividend-row['d1_close'])>.011:
                    reason = '持仓参考价不连续且无已核对公司行动'; break
        if reason:
            excluded.append({'symbol':row['symbol'],'d1_date':day,'reason':reason})
        else:
            entries[day] = row
    cash = initial; prior = initial; peak = initial; positions = []
    daily = []; ledger = []; max_positions = 0; zero_cash = 0
    for day in dates:
        i = index[day]; opening_cash = cash; buy = 0.; sales = 0.; profit = 0.; fees = 0.
        if day in entries:
            r = entries[day]
            assert len(positions)<=1
            carried = len(positions)
            budget = max(0.,cash)*(1. if carried else .5)
            if budget>1e-8:
                buy_amount = budget/(1+fee_rate)
                buy_fee = budget-buy_amount
                lot = dict(r)
                lot.update(lot_id=len(ledger)+1,entry_date=day,buy=r['entry'],cost=budget,
                    buy_amount=buy_amount,buy_fee=buy_fee,sell_fee=None,sell_amount=None,
                    shares=buy_amount/r['entry'],cash_before=cash,cash_after=cash-budget,
                    carried=carried,allocation='余款全买' if carried else '空仓半买',exit_date=None,sell=None,proceeds=None,
                    pnl=None,return_=None,start_i=i,
                    planned_exit=calendar[i+1] if i+1<len(calendar) else None)
                ledger.append(lot); positions.append(lot); cash-=budget; buy=budget
                fees+=buy_fee
                max_positions=max(max_positions,len(positions))
            else:
                zero_cash+=1
        keep=[]; market_value=0.; unrealized=0.
        for lot in positions:
            k=i-lot['start_i']+1; assert k in (1,2)
            close=lot[f'd{k}_close']; value=lot['shares']*close
            lot['mark_date']=day; lot['mark_price']=close
            if k==2:
                sell_fee=value*fee_rate
                dividend_cash=lot['shares']*float(lot.get('d2_cash_dividend',0.) or 0.)
                net=value-sell_fee+dividend_cash
                lot.update(exit_date=day,sell=close,sell_amount=value,sell_fee=sell_fee,
                    dividend_cash=dividend_cash,proceeds=net,pnl=net-lot['cost'],return_=net/lot['cost']-1)
                cash+=net; sales+=net; profit+=net-lot['cost']; fees+=sell_fee
            else:
                keep.append(lot); market_value+=value; unrealized+=value-lot['cost']
        positions=keep; nav=cash+market_value; peak=max(peak,nav)
        assert cash>=-1e-7 and nav>0
        daily.append(dict(date=day,opening_cash=opening_cash,buy=buy,sales=sales,realized=profit,fees=fees,
            cash=cash,market_value=market_value,nav=nav,return_=nav/prior-1,
            drawdown=1-nav/peak,positions=len(positions),unrealized=unrealized))
        prior=nav
    n=pd.DataFrame(daily); l=pd.DataFrame(ledger); monthly=[]; previous=initial
    for month,g in n.groupby(n.date.str[:7],sort=True):
        bought=l[l.entry_date.str.startswith(month)] if len(l) else l
        sold=l[l.exit_date.fillna('').str.startswith(month)] if len(l) else l
        endnav=float(g.nav.iloc[-1])
        monthly.append(dict(month=month,days=len(g),entries=len(bought),closed=len(sold),
            opening_nav=previous,closing_nav=endnav,
            profit=endnav-previous,return_=endnav/previous-1,fees=float(g.fees.sum()),
            drawdown=drawdown(np.r_[previous,g.nav]),sharpe=sharpe(g.return_),
            cash=float(g.cash.iloc[-1]),market_value=float(g.market_value.iloc[-1])))
        previous=endnav
    realized=float(l.pnl.dropna().sum()) if len(l) else 0.
    unrealized=float(n.unrealized.iloc[-1])
    assert np.isclose(prior,initial+realized+unrealized,rtol=0,atol=1e-7)
    max_dd_i=int(n.drawdown.idxmax()); before=n.iloc[:max_dd_i+1]
    high_i=before.nav.idxmax() if before.nav.max()>initial else None
    summary=dict(start=start,first_market_day=dates[0],end=dates[-1],market_days=len(n),
        initial=initial,fee_rate=fee_rate,total_fees=float(n.fees.sum()),signals=len(signals),entries=len(l),
        closed=int(l.exit_date.notna().sum()) if len(l) else 0,pending=len(positions),
        excluded=len(excluded),half_cash_entries=int(l.carried.eq(0).sum()) if len(l) else 0,
        all_cash_entries=int(l.carried.eq(1).sum()) if len(l) else 0,
        cash_skips=zero_cash,min_entry=float(l.cost.min()) if len(l) else None,
        max_entry=float(l.cost.max()) if len(l) else None,
        max_positions=max_positions,final_nav=prior,total_profit=prior-initial,total_return=prior/initial-1,
        realized=realized,unrealized=unrealized,closing_cash=cash,
        closing_market_value=float(n.market_value.iloc[-1]),max_drawdown=float(n.drawdown.max()),
        drawdown_peak_date=str(n.loc[high_i,'date']) if high_i is not None else start,
        drawdown_trough_date=str(n.loc[max_dd_i,'date']),sharpe=sharpe(n.return_),
        positive_months=sum(m['return_']>0 for m in monthly),months=len(monthly),
        closed_win_rate=float(l.return_.dropna().gt(0).mean()) if len(l) else None,
        closed_mean_return=float(l.return_.dropna().mean()) if len(l) else None)
    return summary,monthly,n,l,excluded


def main():
    OUT.mkdir(exist_ok=True)
    calendar=json.loads((BASE/'calendar.json').read_text())
    spec=dict(name='R23B_CNY1m_cash_rotation_D2close',start='2026-01-01',end=calendar[-1],
        initial=INITIAL,budget='No carried position at morning: invest half all available cash. One carried position: invest all available cash. No fixed ticket, no debt; fractional shares. User corrected fixed500k interpretation before final report.',
        sequence='Buy in the original D1 eligible minute; D2 (next trading day after D1) close sell. Closing sale cash is available next morning, never retroactively.',
        strategy='R18 frozen monthly RF + original R19 6train/2validation monthly shape rule; chronological Top3 fallback; then body5>-8%; no replacement after this extra gate rejects.',
        entry='Original 09:36..09:40 bar-label open below cap, positive minute volume. 09:36 label means 09:35..09:36; full fill at bar open is assumed, not orderbook evidence.',
        endpoint='No forced final sale. Retain pending position at last known D1 close; do not impute unseen D2.',
        fees='Gross: no commissions, taxes, slippage or price impact; D2 close sale fully filled by assumption.',
        sharpe='sqrt(252)*mean(all market-day account simple returns)/sample_sd(ddof=1); rf=0; include idle days, initial NAV anchor.',
        drawdown='Closing marked-to-market NAV; overall peak starts at initial NAV; monthly peak resets to previous month closing NAV.',
        monthly='Actual holding calendar month, cross-month position marked daily. October is partial.',
        research='No retraining or selection on new results. Original monthly predictions are held out for their model/rule, but period and body gate were previously researched; not a new blind test.',
        anomalies='Existing reference-price continuity policy; exclude broken observations without replacement, record ex-post exclusions. Unknown future endpoint remains open.',
        source_sha256=sha(SOURCE/'policy_picks.pkl'),calendar_sha256=sha(BASE/'calendar.json'),
        rules_sha256=sha(SOURCE/'rules.json'),models_sha256=sha(SOURCE/'models.json'))
    sp=OUT/'specification.json'
    if sp.exists():assert json.loads(sp.read_text())==spec
    else:dump(sp,spec)
    f=pd.read_pickle(SOURCE/'policy_picks.pkl')
    f=f[f.policy.eq('body8')&f.d1_date.between(spec['start'],spec['end'])].copy()
    assert f.shape_pass.all() and f.body_keep.all() and f.d0_rank.le(3).all()
    f.to_pickle(OUT/'signals.pkl')
    s,m,n,l,e=replay(f,calendar)
    dump(OUT/'summary.json',s);dump(OUT/'monthly.json',m);dump(OUT/'excluded.json',e)
    n.to_pickle(OUT/'daily_nav.pkl');l.to_pickle(OUT/'ledger.pkl')
    print(json.dumps(s,ensure_ascii=False,indent=2))
    print(pd.DataFrame(m)[['month','entries','profit','return_','drawdown','sharpe']].to_string(index=False))


if __name__=='__main__':main()
