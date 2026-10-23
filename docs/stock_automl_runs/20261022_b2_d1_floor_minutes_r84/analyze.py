from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd, numpy as np, hashlib, json
R=Path('/data/cyris/work/b2_d1_floor_minutes_r84_20261022')
B=Path('/data/cyris/work/b2_prebuy_volume_warning_r83_20261022')
P=Path('/data/cyris/work/b2_news_flow_combo_r75_20261022')
S=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
p=pd.read_pickle(P/'panel306.pkl')
out=pd.read_csv(B/'306笔D0_D1买前指标与结果.csv')
s=pd.read_pickle(S/'joined_306.pkl')
assert len(p)==len(out)==len(s)==306
o=out.set_index(out['股票']+'|'+out['D0选中日'])
s=s.set_index('id')
audit=json.loads((B/'D1分钟源SHA.json').read_text())
rows=[]
for _,r in p.iterrows():
 code=str(r.symbol).zfill(6);day=str(r.d1_date)[:10]
 ident=str(r.stock)+'|'+str(r.d0_date)
 bpath=B/'d1_minutes'/f'{day}_{code}.csv.gz'
 assert str(bpath) in audit
 assert hashlib.sha256(bpath.read_bytes()).hexdigest()==audit[str(bpath)]
 b=pd.read_csv(bpath,compression='gzip')
 assert len(b)==240
 times=b.time.astype(str).str[11:16]
 assert times.iloc[:5].tolist()==['09:31','09:32','09:33','09:34','09:35']
 rate=Decimal(str(float(r.limit_rate)))
 floor=float((Decimal(str(float(r.d1_preclose)))*(Decimal(1)-rate)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
 assert b[['open','high','low','close']].to_numpy().min()>=floor-.005,(ident,floor)
 assert abs(float(b.open.iloc[0])-float(r.d1_open))<.005,(ident,'open')
 assert abs(float(b.close.iloc[-1])-float(r.d1_close))<.005,(ident,'close')
 close=(b.close.iloc[:5].to_numpy(dtype=float)<=floor+.005)
 touch=(b.low.iloc[:5].to_numpy(dtype=float)<=floor+.005)
 lock=(b.high.iloc[:5].to_numpy(dtype=float)<=floor+.005)
 assert all(~lock|close) and all(~close|touch)
 x=o.loc[ident];z=s.loc[ident]
 assert str(x['D1'])==day
 entryidx=int(str(r.entry_clock)[3:])-31
 assert 4<=entryidx<=8
 pre=b.iloc[:entryidx+1]
 rec={'股票':r.stock,'D0选中日':str(r.d0_date),'D1买入日':day,'D2卖出日':str(x.D2),'阶段':str(x.split),
      'D1前收':float(r.d1_preclose),'D1跌停价':floor,'D1涨跌停幅度':float(r.limit_rate),
      'D1开盘价':float(b.open.iloc[0]),'开盘即跌停':int(float(b.open.iloc[0])<=floor+.005),
      'D1_0935收盘价':float(b.close.iloc[4]),'0935收盘跌停':int(close[4]),
      'D1买入价代理':float(r.entry),'实际判断时刻':str(r.entry_clock),'实际成交代理时刻':str(r.entry_time),
      '前四分钟收盘跌停数':int(close[:4].sum()),'前五分钟收盘跌停数':int(close.sum()),
      '前四分钟最低触板数':int(touch[:4].sum()),'前五分钟最低触板数':int(touch.sum()),
      '前四分钟整根封死数':int(lock[:4].sum()),'前五分钟整根封死数':int(lock.sum()),
      '原判断前收盘跌停数':int((pre.close.to_numpy(dtype=float)<=floor+.005).sum()),
      'D1收盘跌停':int(float(r.d1_close)<=floor+.005),
      'D1全天一价封死':int((b.high.to_numpy(dtype=float)<=floor+.005).all()),
      'D2全天一价封死':int(x['D2全天一价封死']),'D2收盘跌停':int(x['D2收盘跌停']),
      'D2D3连续收盘跌停':int(x['D2D3连续收盘跌停']),
      '原卖法费后收益_pct':float(x['原卖法费后收益_pct']),
      '原正式收盘卖法费后收益_pct':(float(z.d2_close)/float(z.entry)*.999/1.001-1)*100,
      '既有D1_Q75预警':int(x.fit_sens_D1_q75),
      'D2上午7否则收盘_卖价未经成交核验':int(z.seven_unverified)}
 for i in range(5):
  key=f'09:{31+i:02d}'
  rec[key+'收盘跌停']=int(close[i])
  rec[key+'最低触跌停']=int(touch[i])
  rec[key+'整根封死']=int(lock[i])
 rows.append(rec)
d=pd.DataFrame(rows)
assert len(d)==306 and d[['股票','D0选中日']].duplicated().sum()==0
assert [int(d[c].sum()) for c in ['D2全天一价封死','D2收盘跌停','D2D3连续收盘跌停']]==[14,52,20]
assert set(d.loc[d['前四分钟整根封死数'].eq(4),'股票']+'|'+d.loc[d['前四分钟整根封死数'].eq(4),'D1买入日'])=={
'002868.SZ|2022-08-08','600721.SH|2023-10-17','002181.SZ|2024-12-20','601212.SH|2026-01-30','603137.SH|2026-07-16'}
d.to_csv(R/'306笔D1开盘五分钟逐笔.csv',index=False,encoding='utf-8-sig')
def summary(g):
 r=g['原卖法费后收益_pct']
 return pd.Series({'笔数':len(g),'D2全天一价封死笔数':int(g['D2全天一价封死'].sum()),
                   'D2全天一价封死率_pct':g['D2全天一价封死'].mean()*100,
                   'D2收盘跌停笔数':int(g['D2收盘跌停'].sum()),
                   'D2收盘跌停率_pct':g['D2收盘跌停'].mean()*100,
                   'D2D3连续收盘跌停笔数':int(g['D2D3连续收盘跌停'].sum()),
                   'D2D3连续收盘跌停率_pct':g['D2D3连续收盘跌停'].mean()*100,
                   '费后盈利笔数':int((r>0).sum()),'费后盈利率_pct':(r>0).mean()*100,
                   '费后笔均_pct':r.mean(),'费后中位_pct':r.median(),
                   '若禁买避亏元_每笔1万':float(-r[r<0].sum()*100),
                   '若禁买少赚元_每笔1万':float(r[r>0].sum()*100),
                   '若禁买净增元_每笔1万':float(-r.sum()*100),
                   '既有D1_Q75已预警笔数':int(g['既有D1_Q75预警'].sum())})
for name,col in [('五分钟收盘次数','前五分钟收盘跌停数'),('四分钟收盘次数','前四分钟收盘跌停数'),('五分钟触板次数','前五分钟最低触板数'),('五分钟整根次数','前五分钟整根封死数'),('开盘跌停','开盘即跌停'),('0935跌停','0935收盘跌停')]:
 t=d.groupby(col,dropna=False).apply(summary,include_groups=False).reset_index()
 t.to_csv(R/(name+'.csv'),index=False,encoding='utf-8-sig')
 print('\n',name)
 print(t[[col,'笔数','D2全天一价封死笔数','D2收盘跌停笔数','D2D3连续收盘跌停笔数','费后盈利笔数','费后笔均_pct','若禁买净增元_每笔1万']].to_string(index=False))
flags={
 '开盘即跌停':d['开盘即跌停'].eq(1),
 '前四分钟任一收盘跌停':d['前四分钟收盘跌停数'].ge(1),
 '前四分钟至少2根收盘跌停':d['前四分钟收盘跌停数'].ge(2),
 '前四分钟4根全跌停':d['前四分钟收盘跌停数'].eq(4),
 '前五分钟任一收盘跌停':d['前五分钟收盘跌停数'].ge(1),
 '前五分钟至少2根收盘跌停':d['前五分钟收盘跌停数'].ge(2),
 '前五分钟5根全跌停':d['前五分钟收盘跌停数'].eq(5),
 '09:35收盘跌停':d['0935收盘跌停'].eq(1),
 '前五分钟任一最低触板':d['前五分钟最低触板数'].ge(1),
}
subs=[]
for name,flag in flags.items():
 for phase,g in [('全306笔',d),('2022-2024训练',d[d.阶段.eq('train')]),('2025验证',d[d.阶段.eq('valid')]),('2026已观察',d[d.阶段.eq('test')])]:
  x=g.loc[flag.loc[g.index]]
  if not len(x):continue
  z=summary(x);z['规则']=name;z['期间']=phase;z['剩余D2全天封死']=int(g['D2全天一价封死'].sum()-x['D2全天一价封死'].sum())
  z['增量捕获_旧D1Q75未提示的封死']=int(((x['既有D1_Q75预警'].eq(0)) & (x['D2全天一价封死'].eq(1))).sum())
  subs.append(z)
pd.DataFrame(subs).to_csv(R/'候选禁买规则分期得失.csv',index=False,encoding='utf-8-sig')
print('\nflagged')
print(d[d['前五分钟收盘跌停数'].ge(1)][['股票','D1买入日','开盘即跌停','前五分钟收盘跌停数','前五分钟最低触板数','D1收盘跌停','D2全天一价封死','D2收盘跌停','D2D3连续收盘跌停','原卖法费后收益_pct','既有D1_Q75预警']].to_string(index=False))
