from pathlib import Path
import gzip,json,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;F=R/'outputs/D0分时形态研究_20261113';B=R/'outputs/b2连续两日全量回放_20261113';names=json.loads((F/'字段.json').read_text());parts=[]
for month in ['2026-10','2026-11']:
 x=pd.read_pickle(B/'近期输入'/month/'test_samples.pkl.gz');x=x[x.d0_date.ge('2026-10-26') & ~x.stk_code.str.endswith('.BJ')];parts.append(x[['stk_code','symbol','d0_date','close']])
x=pd.concat(parts,ignore_index=True);daily=pd.read_pickle(R/'outputs/b2_d0_live_rebuild_20261112/daily_source.pkl.gz');daily['trade_date']=daily.trade_date.astype(str);daily=daily[['stk_code','trade_date','open','high','low','close','volume','amount']].rename(columns={'trade_date':'d0_date','open':'日开','high':'日高','low':'日低','close':'日收','volume':'日量','amount':'日额'});daily[['日开','日高','日低','日收','日量','日额']]=daily[['日开','日高','日低','日收','日量','日额']].astype(float);x=x.merge(daily,on=['stk_code','d0_date'],how='left',validate='one_to_one');rows=[];expected=list(range(571,691))+list(range(781,901))
for code,events in x.groupby('stk_code'):
 path=P/'研究行情分钟'/(code+'.json.gz')
 if not path.exists():continue
 raw=json.loads(gzip.decompress(path.read_bytes()))['bars'];bars=[]
 for a in raw:
  t=a['sttDateTime'];bars.append({'date':str(t['iDate']),'minute':t['shtTime'],'open':a['fOpen'],'high':a['fHigh'],'low':a['fLow'],'close':a['fClose'],'volume':a['lVolume'],'amount':a['fAmount']})
 b=pd.DataFrame(bars)
 if b.empty:continue
 for row in events.itertuples():
  g=b[b.date.eq(row.d0_date.replace('-',''))].sort_values('minute').copy();clock=g.minute.tolist();r={'qdc_symbol':row.stk_code.replace('.SH','.XSHG').replace('.SZ','.XSHE'),'d0_date':row.d0_date,'分钟记录数':len(g),'分时有效':False}
  if len(g):
   invalid=(g[['open','high','low','close']].le(0).any(axis=1)|g.high.lt(g[['open','close']].max(axis=1)-1e-5)|g.low.gt(g[['open','close']].min(axis=1)+1e-5)|g.volume.lt(0)|g.amount.lt(0)).any();r.update({'分钟首开':g.open.iloc[0],'分钟最高':g.high.max(),'分钟最低':g.low.min(),'分钟末收':g.close.iloc[-1],'分钟总量':g.volume.sum(),'分钟总额':g.amount.sum()});valid=(clock==expected and not invalid and g.volume.sum()>0 and abs(g.close.iloc[-1]-row.close)<=.011);r['分时有效']=valid
   if valid:
    t=g.minute;first=g[t.le(600)];last=g[t.gt(870)];morning=g[t.le(690)];afternoon=g[t.ge(780)];total=g.volume.sum();changes=np.log(g.close/g.close.shift());abs_path=changes.abs().sum();hit=g[g.high.ge(row.close-.005)];seal=g.close.ge(row.close-.005)
    r.update({'首30分钟涨幅':first.close.iloc[-1]/g.open.iloc[0]-1,'末30分钟涨幅':g.close.iloc[-1]/last.open.iloc[0]-1,'上午涨幅':morning.close.iloc[-1]/g.open.iloc[0]-1,'下午涨幅':g.close.iloc[-1]/afternoon.open.iloc[0]-1,'首30分钟成交量占比':first.volume.sum()/total,'末30分钟成交量占比':last.volume.sum()/total,'下午成交量占比':afternoon.volume.sum()/total,'下午相对上午成交量':afternoon.volume.sum()/morning.volume.sum() if morning.volume.sum()>0 else np.nan,'上涨K成交量方向比':(np.sign(g.close-g.open)*g.volume).sum()/total,'上涨分钟比例':g.close.gt(g.open).mean(),'全日分钟波动':np.sqrt((changes**2).sum()),'走势效率':abs(np.log(g.close.iloc[-1]/g.open.iloc[0]))/abs_path if abs_path>0 else np.nan,'触板时间':int(hit.minute.iloc[0])-570 if len(hit) else np.nan,'收板分钟占比':seal.mean(),'分钟收盘开板次数':(g.close.shift().ge(row.close-.005)&~seal).sum(),'封板时成交量占比':g.loc[seal,'volume'].sum()/total,'尾盘板下分钟占比':last.close.lt(row.close-.005).sum()/30})
  for n in names:r.setdefault(n,np.nan)
  rows.append(r)
f=pd.DataFrame(rows);assert not f.duplicated(['qdc_symbol','d0_date']).any();f.to_pickle(P/'研究行情分时因子.pkl.gz',compression='gzip');x['qdc_symbol']=x.stk_code.str.replace('.SH','.XSHG',regex=False).str.replace('.SZ','.XSHE',regex=False);z=x.merge(f,on=['qdc_symbol','d0_date'],how='left',validate='one_to_one');z['收盘差']=(z.分钟末收-z.日收).abs();z['最高差']=(z.分钟最高-z.日高).abs();z['最低差']=(z.分钟最低-z.日低).abs();z['量比']=z.分钟总量/z.日量.replace(0,np.nan);coverage=z.groupby('d0_date').agg(候选=('symbol','size'),可用=('分时有效','sum'),收盘差超1分=('收盘差',lambda s:int(s.gt(.011).sum())),最高差超1分=('最高差',lambda s:int(s.gt(.011).sum())),最低差超1分=('最低差',lambda s:int(s.gt(.011).sum()))).reset_index();coverage.to_csv(P/'研究行情逐日审计.csv',index=False);z[['stk_code','d0_date','分钟记录数','分时有效','收盘差','最高差','最低差','量比']].to_csv(P/'研究行情逐股审计.csv',index=False);print(coverage.to_string(index=False));print('量比',z.loc[z.分时有效,'量比'].describe().to_string())
old=pd.read_pickle(F/'D0分时因子_含十月.pkl.gz');both=f[f.d0_date.lt('2026-11-01')].merge(old[['qdc_symbol','d0_date','分时有效']+names],on=['qdc_symbol','d0_date'],suffixes=('_研究','_旧'),validate='one_to_one');both=both[both.分时有效_研究.eq(True)&both.分时有效_旧.eq(True)];d=[]
for n in names:
 delta=(both[n+'_研究']-both[n+'_旧']).abs();d.append({'因子':n,'共同股票日':int(delta.notna().sum()),'不同股次':int(delta.gt(1e-8).sum()),'绝对差中位':float(delta.median()),'绝对差最大':float(delta.max())})
pd.DataFrame(d).to_csv(P/'十月分时因子双源差异.csv',index=False);print('十月共同有效',len(both));print(pd.DataFrame(d).to_string(index=False))
