from pathlib import Path
import json,hashlib
import numpy as np,pandas as pd
R=Path('/data/cyris/work/b2_idle_extend_r77_20261022')
P=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
D=Path('/data/cyris/work/b2_d2_intraday_r67_20261021')
s=pd.read_pickle(P/'joined_306.pkl').copy().reset_index(drop=True)
b=pd.read_csv(R/'inputs/逐笔D3涨幅与连板.csv',dtype={'symbol':str})
daily=pd.read_pickle('/data/cyris/work/b2_d2_limit_follow_r69_20261021/daily.pkl')
calendar=json.loads((D/'inputs/calendar.json').read_text())
s['轮空']=~s.d2_date.isin(set(s.d1_date))
s['股票']=s.stk_code.str[:6]
s=s.merge(b[['symbol','D0','d2_limit_up','d3_limit_up','consecutive_up']],left_on=['股票','d0_date'],right_on=['symbol','D0'],validate='one_to_one')
s=s.merge(daily[['trade_date','stk_code','preclose','volume','is_limit_price']].rename(columns={'trade_date':'d3_date','preclose':'d3_preclose','volume':'d3_volume','is_limit_price':'d3_limit_flag'}),on=['d3_date','stk_code'],validate='one_to_one')
assert len(s)==306 and s.轮空.sum()==112
assert np.isclose(s.d3_close,s.merge(daily[['trade_date','stk_code','close']].rename(columns={'trade_date':'d3_date'}),on=['d3_date','stk_code'],validate='one_to_one')['close']).all()
assert (s.loc[~np.isclose(s.d3_preclose,s.d2_close),'轮空']==False).all()
assert (s.d3_limit_flag.eq(2)==False).sum()>0
fee=.001;cost_ratio=(1-fee)/(1+fee)
s['D2费后收益']=s.d2_close/s.entry*cost_ratio-1
s['D3费后收益_价格假设']=s.d3_close/s.entry*cost_ratio-1
s['多持一日差_百分点']=100*(s['D3费后收益_价格假设']-s['D2费后收益'])
s['D3收盘跌停']=s.d3_limit_flag.eq(2)
s['D2月末跨D3']=s.d2_date.str[:7]!=s.d3_date.str[:7]
s['上午7%触发']=s.trigger7.ne('')
s['7%在0934前']=s.trigger7.ne('') & s.sell7_time.le('09:34')
s['D2卖出未核实']=s.close_unverified
s['D3卖出未核实_跌停或零日量']=s['D3收盘跌停'] | s.d3_volume.le(0)
assert s.loc[s.轮空,'D3卖出未核实_跌停或零日量'].sum()==17
assert s.loc[s.轮空,'D2月末跨D3'].sum()==3
s[['id','股票','d0_date','d1_date','d2_date','d3_date','轮空','entry','d2_close','d3_close','D2费后收益','D3费后收益_价格假设','多持一日差_百分点','d2_limit_up','d3_limit_up','consecutive_up','上午7%触发','7%在0934前','D2卖出未核实','D3卖出未核实_跌停或零日量','D2月末跨D3']].to_csv(R/'306笔逐笔对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')

def replay(frame, extend,mode='close',start='2022-01-01'):
    cash=1e6;peak=cash;hold={};events={};ledger=[];dayrows=[]
    for row in frame.to_dict('records'):
        events.setdefault(row['d1_date'],[]).append((row['buy_clock'],0,row))
        delayed=bool(extend and row['轮空'])
        sell_day=row['d3_date'] if delayed else row['d2_date']
        sell_clock='15:00' if mode=='close' or delayed else row['sell7_time']
        events.setdefault(sell_day,[]).append((sell_clock,1,row))
    end=max(x['d3_date'] if extend and x['轮空'] else x['d2_date'] for x in frame.to_dict('records'))
    for day in (d for d in calendar if start<=d<=end):
        for clock,kind,row in sorted(events.get(day,[]),key=lambda x:(x[0],x[1])):
            if kind==0:
                cost=cash*(.5 if not hold else 1.)
                assert cost>0,(day,row['id'],cash)
                shares=cost/((1+fee)*row['entry'])
                cash-=cost
                hold[row['id']]=(shares,cost,row)
            else:
                shares,cost,old=hold.pop(row['id'])
                delayed=bool(extend and row['轮空'])
                price=row['d3_close'] if delayed else row['d2_close'] if mode=='close' else row['sell7']
                proceeds=shares*price*(1-fee)
                cash+=proceeds
                ledger.append(dict(id=row['id'],D0=row['d0_date'],D1=row['d1_date'],D2=row['d2_date'],D3=row['d3_date'],轮空=row['轮空'],延期=delayed,买入投入元=cost,卖出假设现金元=proceeds,损益元=proceeds-cost,费后收益率=proceeds/cost-1,卖出日=day,卖出价假设=price,D3收盘跌停=row['D3收盘跌停'],D3卖出未核实=delayed and row['D3卖出未核实_跌停或零日量']))
        marked=0
        for shares,cost,row in hold.values():
            mark=row['d1_close'] if day==row['d1_date'] else row['d2_close'] if day==row['d2_date'] else row['d3_close']
            marked+=shares*mark
        nav=cash+marked
        peak=max(peak,nav)
        dayrows.append(dict(date=day,nav=nav,cash=cash,drawdown=1-nav/peak,open_positions=len(hold)))
    assert not hold
    l=pd.DataFrame(ledger);v=pd.DataFrame(dayrows)
    assert len(l)==len(frame)
    assert abs(v.nav.iloc[-1]-(1e6+l['损益元'].sum()))<1e-6
    return dict(账户收益率=cash/1e6-1,最大回撤=v.drawdown.max(),期末元=cash,结清日=end),l,v

accounts=[];years=[];ledgers={}
for mode in ['close','morning7']:
    for extend in [False,True]:
        label=f'{mode}_'+('D3轮空延期' if extend else '原D2卖出')
        z,l,v=replay(s,extend,mode)
        accounts.append(dict(口径=label,**z,买入笔数=len(l),延期笔数=int(l.延期.sum()),D3未核实卖出=int(l.D3卖出未核实.sum())))
        l.to_csv(R/f'全期_{label}_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
        v.to_csv(R/f'全期_{label}_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
        ledgers[label]=l
        for year,g in s.groupby('d0_year'):
            z0,l0,v0=replay(g,extend,mode,start=year+'-01-01')
            years.append(dict(口径=label,D0年=year,**z0,买入笔数=len(l0),延期笔数=int(l0.延期.sum()),D3未核实卖出=int(l0.D3卖出未核实.sum())))
            l0.to_csv(R/f'{year}_{label}_账本.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
            v0.to_csv(R/f'{year}_{label}_逐日.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
    prior=pd.read_csv(P/f'全期_{mode}_baseline_daily.csv')
    here=pd.read_csv(R/f'全期_{mode}_原D2卖出_逐日.csv')
    merged=prior.merge(here,on='date',validate='one_to_one',suffixes=('_prior','_new'))
    assert len(merged)==len(prior)==len(here)
    for col in ['nav','cash','drawdown','open_positions']:
        assert np.allclose(merged[col+'_prior'],merged[col+'_new'],atol=1e-6,rtol=0),(mode,col,np.max(abs(merged[col+'_prior']-merged[col+'_new'])))
    annual=pd.read_csv(P/'逐年独立重置_全部方案.csv')
    for year in s.d0_year.unique():
        old=annual[(annual.卖法==mode)&(annual.方案=='baseline')&(annual.D0年.astype(str)==year)].iloc[0]
        new=next(x for x in years if x['口径']==f'{mode}_原D2卖出' and x['D0年']==year)
        assert abs(old['账户独立重置收益率']-new['账户收益率'])<1e-9
        assert abs(old['账户独立重置最大回撤']-new['最大回撤'])<1e-9
pd.DataFrame(accounts).to_csv(R/'全期账户对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
pd.DataFrame(years).to_csv(R/'逐年独立账户对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')

groups=[]
for name,g in [('轮空',s[s.轮空]),('有新买入',s[~s.轮空]),('轮空且D2收盘涨停',s[s.轮空 & s.d2_limit_up]),('轮空且D2未收盘涨停',s[s.轮空 & ~s.d2_limit_up]),('轮空且D2上午触发7%',s[s.轮空 & s['上午7%触发']]),('轮空且D2上午未触发7%',s[s.轮空 & ~s['上午7%触发']])]:
    groups.append(dict(分组=name,笔数=len(g),D2费后笔均收益=g['D2费后收益'].mean(),D3费后笔均收益_估值=g['D3费后收益_价格假设'].mean(),多持一天平均差_百分点=g['多持一日差_百分点'].mean(),每笔1万元合计差元=g['多持一日差_百分点'].sum()*100,D2收盘涨停=int(g.d2_limit_up.sum()),D3收盘涨停=int(g.d3_limit_up.sum()),连续涨停=int(g.consecutive_up.sum()),D3收盘跌停=int(g['D3收盘跌停'].sum()),D3卖出未核实=int(g['D3卖出未核实_跌停或零日量'].sum())))
pd.DataFrame(groups).to_csv(R/'轮空分组对照.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
y=[]
for year,g in s[s.轮空].groupby('d0_year'):
    y.append(dict(D0年=year,轮空笔数=len(g),D2费后笔均收益=g['D2费后收益'].mean(),D3费后笔均收益_估值=g['D3费后收益_价格假设'].mean(),多持一天平均差_百分点=g['多持一日差_百分点'].mean(),每笔1万元合计差元=g['多持一日差_百分点'].sum()*100,连续涨停=int(g.consecutive_up.sum()),D3收盘跌停=int(g['D3收盘跌停'].sum())))
pd.DataFrame(y).to_csv(R/'轮空逐年逐笔差额.csv',index=False,encoding='utf-8-sig',float_format='%.10f')
print(pd.DataFrame(accounts).to_string(index=False))
print(pd.DataFrame(years).to_string(index=False))
print(pd.DataFrame(groups).to_string(index=False))
print(pd.DataFrame(y).to_string(index=False))
print('cross',s.loc[s.轮空 & s.D2月末跨D3,['id','d0_date','d2_date','d3_date','多持一日差_百分点']].to_string(index=False))
(R/'verification.json').write_text(json.dumps(dict(source_R76_sha256=hashlib.sha256((P/'joined_306.pkl').read_bytes()).hexdigest(),source_board_csv_sha256=hashlib.sha256((R/'inputs/逐笔D3涨幅与连板.csv').read_bytes()).hexdigest(),cohort=306,idle=112,d3_floor_idle=17,dividend_preclose_mismatch_idle=0,baseline_daily_reproduced_for=['close','morning7'],baseline_annual_reproduced_count=10,month_cross_count=3,assumed_fills=True),ensure_ascii=False,indent=2)+'\n')
