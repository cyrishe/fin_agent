from pathlib import Path
import glob
import hashlib
import json
import numpy as np
import pandas as pd

R=Path('/data/cyris/work')
O=R/'b2_prebuy_volume_warning_r83_20261022'
R71=R/'b2_board_volume_r71_20261022'
R72=R/'b2_first_board_flow_r72_20261022'
R75=R/'b2_news_flow_combo_r75_20261022'
R76=R/'b2_news_flow_years_r76_20261022'
b=pd.read_csv(R71/'B2_V2放行连板量能特征_306笔.csv',dtype={'股票':str})
p=pd.read_pickle(R75/'panel306.pkl')
q=pd.read_csv(R72/'首板前量价资金趋势_709候选.csv',dtype={'股票':str})
pred=pd.read_csv(R75/'306逐笔组合预测.csv')
d1=pd.read_csv(O/'D1前四分钟原档复核_306笔.csv',dtype={'股票':str})
ledgers=sorted(glob.glob(str(R76/'20*_morning7_baseline_ledger.csv')))
l=pd.concat([pd.read_csv(f) for f in ledgers],ignore_index=True)
l[['股票','D0选中日']]=l.id.str.split('|',expand=True)
p['股票']=p['stock'];p['D0选中日']=p.d0_date.astype(str)
m=l[['股票','D0选中日','D1','D2','return']].merge(
 b[['股票','D0选中日','首板日','选股日成交量_万股','首板成交量_万股',
    '首板前20日均量_万股','首板前20日最大量_万股','D0量除首板前20日均量',
    '首板量除前20日均量','已连板天数','D0一字','D2D3连续收盘跌停']],
 on=['股票','D0选中日'],validate='one_to_one').merge(
 p[['股票','D0选中日','split','all_locked','close_floor','open_gap','high5','low5','body5','pullback5',
    'early_volume_ratio','early_volume_accel','early_down_volume','position5',
    'close_sealed_minutes','last30_amount_fraction','d0_turnover_pct']],
 on=['股票','D0选中日'],validate='one_to_one').merge(
 q[['股票','D0选中日','pre20_price_return','pre20_volume_trend_log','first_price_impact_per_relvolume']],
 on=['股票','D0选中日'],validate='one_to_one').merge(
 d1[['股票','D0选中日','D1首分钟量_股','D1前四分钟量_股','D1前四K价格一字','D1前四K最高涨幅','D1前四K最低涨幅']],
 on=['股票','D0选中日'],validate='one_to_one')
m['id']=m['股票']+'|'+m['D0选中日']
m=m.merge(pred[['id','union_early_lr01','close_early_lr1','fit_sens_D1_q75']],on='id',validate='one_to_one')
assert len(m)==306
assert m.split.value_counts().to_dict()=={'train':152,'test':98,'valid':54,'purged':2}
m['D2全天一价封死']=m.all_locked.eq('是').astype(int)
m['D2收盘跌停']=m.close_floor.eq('是').astype(int)
assert m['D2全天一价封死'].sum()==14 and m.D2收盘跌停.sum()==52 and m.D2D3连续收盘跌停.sum()==20
m['D1前四K量除板前20日均量']=m['D1前四分钟量_股']/(m['首板前20日均量_万股']*1e4)
m['D0量除板前20日最大量']=m['选股日成交量_万股']/m['首板前20日最大量_万股']
m['D1前四K下限一字']=((m['D1前四K价格一字']==1)&(m['D1前四K最高涨幅']<=-.095)).astype(int)
assert m['D1前四K下限一字'].sum()==5

hist=pd.read_csv(R71/'preboard_daily_source.csv.gz',dtype={'stk_code':str})
pre=pd.read_csv(O/'首板前同刻分钟_逐股日.csv.gz',dtype={'stk_code':str})
assert len(pre)==pre[['trade_date','stk_code']].drop_duplicates().shape[0]
pre=pre.set_index(['trade_date','stk_code'])
ratios=[]
for _,z in m.iterrows():
 h=hist[(hist.stk_code==z['股票'])&(hist['对应首板日']==z['首板日'])].sort_values('trade_date',ascending=False)
 out={'id':z['id']}
 for n in (5,10,20):
  dates=h.head(n).trade_date.tolist()
  if len(dates)<n or not all((d,z['股票']) in pre.index for d in dates):
   out[f'板前{n}同刻完整']=0;continue
  a=pre.loc[[(d,z['股票']) for d in dates]]
  if not a['有效'].eq(1).all() or a['开盘首分钟量_股'].isna().any():
   out[f'板前{n}同刻完整']=0;continue
  out[f'板前{n}同刻完整']=1
  out[f'D1首分钟量除板前{n}同刻均量']=z['D1首分钟量_股']/a['开盘首分钟量_股'].mean()
  out[f'D1首分钟量除板前{n}同刻最大量']=z['D1首分钟量_股']/a['开盘首分钟量_股'].max()
 ratios.append(out)
m=m.merge(pd.DataFrame(ratios),on='id',validate='one_to_one')
m['D0年份']=m['D0选中日'].str[:4]
m['原卖法费后收益_pct']=m['return']*100
m['若禁买则少赚_1万元']=m['return'].clip(lower=0)*10000
m['若禁买则避亏_1万元']=-m['return'].clip(upper=0)*10000
base_cols=['股票','D0选中日','D1','D2','split','首板日','D0年份','原卖法费后收益_pct','D2全天一价封死','D2收盘跌停','D2D3连续收盘跌停','D1前四K下限一字',
           'union_early_lr01','close_early_lr1','fit_sens_D1_q75','D0量除首板前20日均量','首板量除前20日均量',
           'pre20_price_return','D1首分钟量_股','D1前四分钟量_股','early_volume_ratio','early_volume_accel','open_gap','high5','low5','body5','pullback5',
           'D1前四K量除板前20日均量','若禁买则少赚_1万元','若禁买则避亏_1万元']
ratio_cols=[c for c in m.columns if c.startswith('板前') and c.endswith('同刻完整') or c.startswith('D1首分钟量除板前')]
m[base_cols+ratio_cols].sort_values(['D0选中日','股票']).to_csv(O/'306笔D0_D1买前指标与结果.csv',index=False,encoding='utf-8-sig')
print('same clock coverage',m[[f'板前{n}同刻完整' for n in (5,10,20)]].sum().to_dict(),flush=True)
print(pd.crosstab(m.split,m['板前20同刻完整']),flush=True)

def score(g):
 wins=g[g['return']>0];loss=g[g['return']<0]
 return {
  '笔数':len(g),'D2全天封死':int(g.D2全天一价封死.sum()),
  'D2D3连续收盘跌停':int(g.D2D3连续收盘跌停.sum()),
  'D2收盘跌停':int(g.D2收盘跌停.sum()),
  '误拦盈利':len(wins),'误拦收益超过10%':int((g['return']>.1).sum()),
  '少赚_1万元合计':round(wins['return'].sum()*10000,2),
  '避亏_1万元合计':round(-loss['return'].sum()*10000,2),
  '净得_1万元合计':round(-g['return'].sum()*10000,2),
  '已有D1_Q75已提示':int(g.fit_sens_D1_q75.sum())
 }
features=[
('D0','D0量除首板前20日均量'),('D0','D0量除板前20日最大量'),('D0','首板量除前20日均量'),
('D0','已连板天数'),('D0','d0_turnover_pct'),('D0','close_sealed_minutes'),
('D0','last30_amount_fraction'),('D0','pre20_price_return'),('D0','pre20_volume_trend_log'),
('D0','first_price_impact_per_relvolume'),
('D1 09:31','D1首分钟量除板前5同刻均量'),('D1 09:31','D1首分钟量除板前5同刻最大量'),
('D1 09:31','D1首分钟量除板前10同刻均量'),('D1 09:31','D1首分钟量除板前10同刻最大量'),
('D1 09:31','D1首分钟量除板前20同刻均量'),('D1 09:31','D1首分钟量除板前20同刻最大量'),
('D1 09:34','D1前四K量除板前20日均量'),('D1 09:34','early_volume_ratio'),
('D1 09:34','early_volume_accel'),('D1 09:34','open_gap'),('D1 09:34','body5'),
('D1 09:34','pullback5'),('D1 09:34','position5'),('D1 09:34','early_down_volume')]
rows=[]
for clock,col in features:
 train=pd.to_numeric(m.loc[m.split.eq('train'),col],errors='coerce').dropna()
 if len(train)<70 or train.nunique()<10:continue
 for side,qv in [('低20%',.2),('高20%',.8)]:
  threshold=float(train.quantile(qv))
  x=pd.to_numeric(m[col],errors='coerce')
  mark=x.le(threshold) if side=='低20%' else x.ge(threshold)
  for split in ('train','valid','test'):
   g=m[mark&m.split.eq(split)]
   base=m[m.split.eq(split)]
   rows.append({'时点':clock,'指标':col,'侧':side,'训练阈值':threshold,'阶段':split,
                '该阶段可比':int(x[m.split.eq(split)].notna().sum()),'该阶段总笔数':len(base),
                **score(g)})
screen=pd.DataFrame(rows)
screen.to_csv(O/'训练分位固定后_单指标分期误伤与风险.csv',index=False,encoding='utf-8-sig')
for name,flag in [
 ('D1前四K跌停一字',m['D1前四K下限一字'].eq(1)),
 ('D1后两根量比_训练低20%',m.early_volume_accel.le(float(m.loc[m.split.eq('train'),'early_volume_accel'].quantile(.2)))),
 ('D1首分钟同刻最大量比_训练低20%',m['D1首分钟量除板前20同刻最大量'].le(float(m.loc[m.split.eq('train'),'D1首分钟量除板前20同刻最大量'].quantile(.2)))),
 ('D0量比_训练低20%',m['D0量除首板前20日均量'].le(float(m.loc[m.split.eq('train'),'D0量除首板前20日均量'].quantile(.2))))
]:
 for split in ('train','valid','test'):
  g=m[flag&m.split.eq(split)]
  print(name,split,score(g),flush=True)

# Source hashes of existing input assets; the 306 D1 minute hashes and new archive hashes live in separate files.
inputs=[R71/'B2_V2放行连板量能特征_306笔.csv',R71/'preboard_daily_source.csv.gz',
        R72/'首板前量价资金趋势_709候选.csv',R75/'panel306.pkl',R75/'306逐笔组合预测.csv',
        O/'D1前四分钟原档复核_306笔.csv',O/'首板前同刻分钟_逐股日.csv.gz',*map(Path,ledgers)]
hashes={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in inputs}
(O/'输入文件SHA256.json').write_text(json.dumps(hashes,ensure_ascii=False,indent=2))
