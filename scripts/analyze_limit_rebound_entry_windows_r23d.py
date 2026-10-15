"""R23D: append frozen triggers and minute-resolution execution evidence to R23C."""
import json
from datetime import datetime,timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from scripts.replay_limit_rebound_cash_r23 import replay,dump,sha,BASE
from scripts.replay_limit_rebound_fees_r23c import OUT as SOURCE,RATE

OUT=Path('outputs/stock_automl/limit_rebound_windows_r23d_20261015')
RAW=SOURCE.parent/'limit_rebound_shape_top3_r20_20261015/minute_days'
BOOK='百万资金轮换_单边千一费后_R23C_20261015.xlsx'


def interval(end):
    end=datetime.fromisoformat(str(end))
    return (end-timedelta(minutes=1)).strftime('%H:%M')+'–'+end.strftime('%H:%M')


def prefix(flags):
    n=0
    for x in flags:
        if not x:break
        n+=1
    return n


def facts(b,cap,entry_label,shares):
    """Window labels are trade-price evidence, never seconds of quote availability."""
    clock=pd.to_datetime(b.time).dt.strftime('%H:%M')
    w=b[clock.between('09:36','09:40')].reset_index(drop=True)
    assert len(w)==5
    vol=w.volume.gt(0);whole=vol&w.high.lt(cap-.005);below=vol&w.low.lt(cap-.005)
    touch=vol&w.high.ge(cap-.005);open_below=vol&w.open.lt(cap-.005)
    i=int(np.flatnonzero(pd.to_datetime(w.time).dt.strftime('%H:%M').eq(entry_label))[0])
    assert open_below.iloc[i]
    hits=np.flatnonzero(touch.to_numpy()[i:])
    later=np.flatnonzero(open_below.to_numpy()[i+1:])+i+1
    state=['无' if not vol.iloc[j] else '下' if whole.iloc[j] else '混' if below.iloc[j] else '板' for j in range(5)]
    return dict(entry_index=i,entry_interval=interval(w.time.iloc[i]),
        pattern='｜'.join(state),window_remaining=5-i,below_minutes=int(below.iloc[i:].sum()),
        whole_minutes=int(whole.iloc[i:].sum()),whole_prefix=prefix(whole.iloc[i:]),
        first_touch=interval(w.time.iloc[i+hits[0]]) if len(hits) else '至09:40未触',
        entry_whole=bool(whole.iloc[i]),entry_share_ratio=shares/float(w.volume.iloc[i]),
        entry_volume=float(w.volume.iloc[i]),
        delayed_index=int(later[0]) if len(later) else None,
        delayed_price=float(w.open.iloc[later[0]]) if len(later) else None,
        delayed_time=pd.Timestamp(w.time.iloc[later[0]]).strftime('%H:%M') if len(later) else None,
        delayed_whole=bool(whole.iloc[later[0]]) if len(later) else None)


def main():
    OUT.mkdir(exist_ok=True)
    spec=dict(name='R23D_frozen_trigger_execution_window',source_workbook_sha256=sha(SOURCE/BOOK),
        source_signals_sha256=sha(SOURCE/'signals.pkl'),source_ledger_sha256=sha(SOURCE/'ledger.pkl'),
        rules_sha256=sha(SOURCE/'inherited_rules.json'),
        intent='Append columns to original fee workbook. Preserve all original trades, fees, NAV and outputs.',
        window='Original entry bar through 09:40 only; full minute high below cap vs low below cap. Consecutive prefix starts at entry, no post-hoc replacement. First-touch interval in this window.',
        trigger='Frozen D0 monthly original Top3 + R19 monthly rule + body5>-8%; shape computable after completed09:35 bar. Score has no extra probability threshold.',
        liquidity='Original fee-funded number of shares divided by ALL prints of entry minute. Ex-post diagnostic only; volume is not freely available ask liquidity, cap prints also included.',
        delay='Hold same original chosen stock; wait one full bar after original entry, then first positive-volume open below cap up to09:40. No other-stock replacement. Same fee-funded account as sensitivity; original workbook results unchanged.',
        missing='No exact seconds, orderbook, latency or queue proof available. Zero whole minutes does not mean impossible to trade. Future window fields never used to form original signal.')
    sp=OUT/'specification.json'
    if sp.exists():assert json.loads(sp.read_text())==spec
    else:dump(sp,spec)
    l=pd.read_pickle(SOURCE/'ledger.pkl');rules={x['month']:x['rule'] for x in json.loads((SOURCE/'inherited_rules.json').read_text())}
    records=[];bars=[];delay=[];raw_sources=[]
    for r in l.itertuples():
        path=RAW/f'{r.entry_date}_raw.pkl';raw=pd.read_pickle(path)
        b=raw[raw.symbol.eq(r.symbol)&raw.source.eq(r.minute_source)].sort_values('time').reset_index(drop=True)
        assert len(b)==240 and b.time.is_unique
        v=b.iloc[:5];hi=float(v.high.max());lo=float(v.low.min());close=float(v.close.iloc[-1]);op=float(v.open.iloc[0])
        ff=dict(high5=hi/r.d1_preclose-1,low5=lo/r.d1_preclose-1,body5=close/op-1,
            position5=(close-lo)/(hi-lo) if hi-lo>.005 else .5)
        for k,val in ff.items():assert np.isclose(val,getattr(r,k),atol=1e-12,rtol=0),(r.entry_date,k)
        rule=rules[r.model_month];assert rule['text']==r.rule_text
        for name,oper,cut in rule['atoms']:
            assert oper=='>=' and ff[name]>=cut-1e-12,(r.entry_date,name,ff[name],cut)
        assert ff['body5']>-.08+1e-12 and r.d0_rank<=3
        z=facts(b,r.d1_cap,r.entry_time,r.shares)
        w=b.iloc[5:10].copy();entry=w.iloc[z['entry_index']]
        assert float(entry.open)==r.buy
        # Both numerator and denominator are shares, reconciled to daily volume.
        assert np.isclose(b.volume.sum(),r.d1_volume,rtol=.001,atol=1)
        record=dict(symbol=r.symbol,d0_date=r.d0_date,entry_date=r.entry_date,score=r.score,rule=r.rule_text,
            rank=r.d0_rank,net_return=float(r.return_) if pd.notna(r.return_) else None,**ff,**z)
        records.append(record)
        for j,x in w.iterrows():
            bars.append(dict(entry_date=r.entry_date,symbol=r.symbol,time=str(x.time),open=x.open,high=x.high,
                low=x.low,close=x.close,volume=x.volume,cap=r.d1_cap,
                from_entry=j-5>=z['entry_index']))
        raw_sources.append(dict(path=str(path),sha256=sha(path)))
        if z['delayed_index'] is not None:
            rr=r._asdict();rr.update(entry=z['delayed_price'],entry_time=z['delayed_time']);delay.append(rr)
    f=pd.DataFrame(records);f.to_pickle(OUT/'windows.pkl');pd.DataFrame(bars).to_pickle(OUT/'minute_evidence.pkl')
    d=pd.DataFrame(delay);d.to_pickle(OUT/'delay_signals.pkl')
    calendar=json.loads((BASE/'calendar.json').read_text())
    ds,dm,dn,dl,de=replay(d,calendar,fee_rate=RATE)
    dump(OUT/'delay_summary.json',ds);dump(OUT/'delay_monthly.json',dm)
    dn.to_pickle(OUT/'delay_daily_nav.pkl');dl.to_pickle(OUT/'delay_ledger.pkl')
    old=json.loads((SOURCE/'summary.json').read_text())
    dist=lambda c:{str(int(k)):int(v) for k,v in f[c].value_counts().sort_index().items()}
    summary=dict(trades=len(f),all_original_prices_unchanged=True,first5_features_recomputed=len(f)*4,
        rules_passed=len(f),entry_whole=int(f.entry_whole.sum()),entry_mixed=int((~f.entry_whole).sum()),
        continuous_whole_distribution=dist('whole_prefix'),whole_minutes_distribution=dist('whole_minutes'),
        no_whole_minutes=int(f.whole_minutes.eq(0).sum()),whole_at_least3=int(f.whole_minutes.ge(3).sum()),
        first_touch_same_minute=int(f.whole_prefix.eq(0).sum()),
        no_touch_before0940=int(f.first_touch.eq('至09:40未触').sum()),
        buy_share_ratio_median=float(f.entry_share_ratio.median()),buy_share_ratio_p90=float(f.entry_share_ratio.quantile(.9)),
        buy_share_ratio_max=float(f.entry_share_ratio.max()),buy_share_ratio_over10pct=int(f.entry_share_ratio.gt(.1).sum()),
        buy_share_ratio_over100pct=int(f.entry_share_ratio.gt(1).sum()),
        delay_available=len(d),delay_missed=len(f)-len(d),delay_entry_whole=int(f.delayed_whole.eq(True).sum()),
        original_total_return=old['total_return'],original_sharpe=old['sharpe'],original_drawdown=old['max_drawdown'],
        delayed_total_return=ds['total_return'],delayed_sharpe=ds['sharpe'],delayed_drawdown=ds['max_drawdown'],
        scope='Descriptive original106 trades, same-stock one-minute delay; not a fitted filter or new blind test')
    dump(OUT/'analysis.json',summary);dump(OUT/'raw_sources.json',raw_sources)
    # Compact added fields: all rule-dependent observed factors, then timing and size evidence.
    labels={'五分最高涨幅':'最高','五分最低涨幅':'最低','五分实体涨幅':'实体','五分收价位置':'位置',' 且 ':'；','>=':'≥','全部保留':'形态不限'}
    additions=[]
    for r in f.itertuples():
        text=r.rule
        for a,b in labels.items():text=text.replace(a,b)
        text+='；实体>−8%'
        additions.append({'评分':r.score,'触发条件':text,'五分最高':r.high5,'五分最低':r.low5,
            '五分实体':r.body5,'五分位置':r.position5,'买点时段':r.entry_interval,'六至十分':r.pattern,
            '余窗板下分':r.below_minutes,'余窗未触分':r.whole_minutes,'连续未触分':r.whole_prefix,
            '买后首触':r.first_touch,'买量占分量':r.entry_share_ratio,'延后一分可买':int(pd.notna(r.delayed_index))})
    dump(OUT/'added_columns.json',additions)
    notes=[
        'R23D新增信号与窗口：原R23C买卖、费用、净值完全保留。评分为D0随机森林分数，沿用原Top3排序，没有额外评分阈值；触发条件为当月形态门槛＋实体>−8%。',
        '形态在09:35完整五根K线结束后可判。五分最高/最低相对D1前收盘；实体=C09:35/O09:30−1；位置=(C09:35−五分最低价)/(五分最高价−最低价)。',
        '原买点列是K线结束标签：09:36代表09:35–09:36；新增买点时段明确该区间。原买入价为该分钟首笔价代理，未预留计算和下单延迟。',
        '六至十分依次表示09:35–36、36–37、37–38、38–39、39–40：下=有成交且最高低于涨停；混=既有板下成交又触板；板=全部成交在涨停价；无=无成交。',
        '余窗从原买点分钟起至09:40：板下分=有板下成交的K数；未触分=最高价低于涨停的K数；连续未触分=从买点起连续未触板K数；混合分钟不计入未触分。',
        '买后首触给出剩余窗口内首次触涨停的分钟区间；至09:40未触表示在这段没有触及。窗口单位是分钟K根数，不是逐秒连续可成交时长。',
        '买量占分量=本次费后实际买入股数/原买点整分钟成交股数，包含涨停成交量，仅作事后容量参照；不能视为可用卖单、成交率或冲击模型。',
        '延后一分可买：同一只已选股票至少晚一根K后，09:40前仍有开价低于涨停且有量的分钟；不替补其他股票。用于执行敏感性，后续窗口列均未用于选股。',
        f"同股延后一分：{len(d)}/{len(f)}笔仍有买价代理；按原费用/资金轮换重放收益{ds['total_return']:.2%}、回撤{ds['max_drawdown']:.2%}、夏普{ds['sharpe']:.2f}。该敏感性没有替换本表原收益。",
        '容量核对：2026-05-22与10-14的模拟买入股数分别占原买点整分钟成交量117.17%与136.71%，标红提示。即使整段未触板，历史成交量也不能支持原价全额成交的假设。'
    ]
    dump(OUT/'added_notes.json',notes)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
