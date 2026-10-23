from pathlib import Path
import pandas as pd,numpy as np,hashlib,json
R=Path('/data/cyris/work/b2_d1_floor_open_r85_20261022')
A=Path('/data/cyris/work/b2_d1_floor_minutes_r84_20261022')
B=Path('/data/cyris/work/b2_prebuy_volume_warning_r83_20261022')
d=pd.read_csv(A/'306笔D1开盘五分钟逐笔.csv')
sha=json.loads((B/'D1分钟源SHA.json').read_text())
cols=[]
for _,r in d.iterrows():
 path=B/'d1_minutes'/f"{r['D1买入日']}_{str(r['股票'])[:6]}.csv.gz"
 assert sha[str(path)]==hashlib.sha256(path.read_bytes()).hexdigest()
 bars=pd.read_csv(path,compression='gzip')
 assert len(bars)==240
 assert bars.time.astype(str).str[11:16].iloc[:5].tolist()==['09:31','09:32','09:33','09:34','09:35']
 floor=float(r['D1跌停价'])
 opens=bars.open.iloc[:5].to_numpy(dtype=float)
 closes=bars.close.iloc[:4].to_numpy(dtype=float)
 assert opens.min()>=floor-.005 and closes.min()>=floor-.005
 assert abs(opens[0]-float(r['D1开盘价']))<.005
 flags=opens<=floor+.005
 cflags=closes<=floor+.005
 assert int(cflags.sum())==r['前四分钟收盘跌停数']
 z={'股票':r['股票'],'D0选中日':r['D0选中日'],'D1买入日':r['D1买入日'],'D2卖出日':r['D2卖出日'],'阶段':r['阶段'],
    'D1跌停价':floor,'前五根开盘跌停数':int(flags.sum()),'前四根收盘跌停数':int(cflags.sum()),
    '前五根开盘全跌停':int(flags.all()),'前四根收盘全跌停':int(cflags.all()),
    '0935开盘跌停':int(flags[4]),'0934收盘跌停':int(cflags[3]),
    'D2全天一价封死':int(r['D2全天一价封死']),'D2收盘跌停':int(r['D2收盘跌停']),
    'D2D3连续收盘跌停':int(r['D2D3连续收盘跌停']),
    'D1全天一价封死':int(r['D1全天一价封死']),
    '费后旧价收益_pct':float(r['原卖法费后收益_pct']),
    '旧D1_Q75预警':int(r['既有D1_Q75预警']),
    '卖出未核实':int(r['D2上午7否则收盘_卖价未经成交核验']),
    '原判断时刻':r['实际判断时刻']}
 for i,t in enumerate(['09:31','09:32','09:33','09:34','09:35']):
  z[t+'开盘价']=opens[i]
  z[t+'开盘跌停']=int(flags[i])
  if i<4:
   z[t+'收盘跌停']=int(cflags[i])
 cols.append(z)
x=pd.DataFrame(cols)
assert len(x)==306 and x[['股票','D0选中日']].duplicated().sum()==0
x.to_csv(R/'306笔_买前五根开盘和四根收盘.csv',index=False,encoding='utf-8-sig')
def summary(g):
 ret=g['费后旧价收益_pct']
 return pd.Series({'笔数':len(g),'D2全天一价封死':int(g['D2全天一价封死'].sum()),
  'D2全天封死率_pct':g['D2全天一价封死'].mean()*100,
  'D2收盘跌停':int(g['D2收盘跌停'].sum()),'D2D3连续收盘跌停':int(g['D2D3连续收盘跌停'].sum()),
  '盈利笔数':int((ret>0).sum()),'笔均费后收益_pct':ret.mean(),
  '避亏元_每笔1万':float(-ret[ret<0].sum()*100),
  '少赚元_每笔1万':float(ret[ret>0].sum()*100),
  '净得元_每笔1万':float(-ret.sum()*100),'旧D1_Q75预警数':int(g['旧D1_Q75预警'].sum())})
for name,col in [('前五根开盘跌停数','前五根开盘跌停数'),('前四根收盘跌停数','前四根收盘跌停数')]:
 t=x.groupby(col).apply(summary,include_groups=False).reset_index()
 t.to_csv(R/(name+'_分档.csv'),index=False,encoding='utf-8-sig')
 print('\n',name);print(t.to_string(index=False))
rules={
'前五根开盘至少1根跌停':x['前五根开盘跌停数'].ge(1),
'前五根开盘至少2根跌停':x['前五根开盘跌停数'].ge(2),
'前五根开盘至少3根跌停':x['前五根开盘跌停数'].ge(3),
'前五根开盘至少4根跌停':x['前五根开盘跌停数'].ge(4),
'前五根开盘全跌停':x['前五根开盘跌停数'].eq(5),
'前四根收盘4根全跌停':x['前四根收盘跌停数'].eq(4),
'09:35开盘跌停':x['0935开盘跌停'].eq(1),
'09:34收盘跌停':x['0934收盘跌停'].eq(1)}
rr=[]
for rule,flag in rules.items():
 for phase,g in [('全部',x),('2022-24训练',x[x.阶段.eq('train')]),('2025验证',x[x.阶段.eq('valid')]),('2026已观察',x[x.阶段.eq('test')])]:
  z=g.loc[flag.loc[g.index]]
  if len(z)==0:continue
  q=summary(z);q['规则']=rule;q['阶段']=phase
  q['新增捕获_旧Q75未提示全天封死']=int(((z['旧D1_Q75预警']==0)&(z['D2全天一价封死']==1)).sum())
  rr.append(q)
pd.DataFrame(rr).to_csv(R/'各买前信号分期经济得失.csv',index=False,encoding='utf-8-sig')
print('\n all5 open')
print(x[x['前五根开盘跌停数'].eq(5)][['股票','D1买入日','前四根收盘跌停数','D2全天一价封死','费后旧价收益_pct']].to_string(index=False))
print('\n open 1-4')
print(x[x['前五根开盘跌停数'].between(1,4)][['股票','D1买入日','前五根开盘跌停数','前四根收盘跌停数','0935开盘跌停','D2全天一价封死','费后旧价收益_pct']].to_string(index=False))
print('\n diff all5open vs all4close')
print(x[x['前五根开盘全跌停'].ne(x['前四根收盘全跌停'])][['股票','D1买入日','前五根开盘跌停数','前四根收盘跌停数']].to_string(index=False))
