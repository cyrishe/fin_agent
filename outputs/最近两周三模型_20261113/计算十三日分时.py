from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import json,hashlib,requests,pandas as pd,numpy as np
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;F=R/'outputs/D0分时形态研究_20261113';x=pd.read_pickle(P/'十三日候选日线.pkl.gz');names=json.loads((F/'字段.json').read_text());url='http://jzyzwup.upoem1.com/json/hq_marketdata/kLineData';expected=list(range(571,691))+list(range(781,901))
def fetch(row):
 code=row['stk_code'];p={'stReq':{'stHeader':{'shtMarket':1 if code.endswith('.SH') else 0},'sCode':row['symbol'],'eLineType':1,'shtStartxh':0,'shtWantNum':260}};rr=requests.post(url,json=p,timeout=20);rr.raise_for_status();raw=rr.json().get('stRsp',{}).get('vAnalyData',[]);b=[]
 for a in raw:
  t=a['sttDateTime'];
  if t['iDate']==20261113 and (571<=t['shtTime']<=690 or 781<=t['shtTime']<=900):b.append({'minute':t['shtTime'],'open':a['fOpen'],'high':a['fHigh'],'low':a['fLow'],'close':a['fClose'],'volume':a['lVolume'],'amount':a['fAmount']})
 g=pd.DataFrame(b).sort_values('minute');cap=float(row['close']);out={'qdc_symbol':code.replace('.SH','.XSHG').replace('.SZ','.XSHE'),'d0_date':'2026-11-13','分钟数':len(g),'来源SHA256':hashlib.sha256(rr.content).hexdigest(),'分时有效':False};valid=len(g)==240 and g.minute.tolist()==expected and g.volume.sum()>0 and abs(g.close.iloc[-1]-cap)<=.011 and not (g[['open','high','low','close']].le(0).any(axis=1)|g.high.lt(g[['open','close']].max(axis=1)-1e-5)|g.low.gt(g[['open','close']].min(axis=1)+1e-5)|g.volume.lt(0)|g.amount.lt(0)).any();out['分时有效']=bool(valid)
 if valid:
  t=g.minute;first=g[t.le(600)];last=g[t.gt(870)];morning=g[t.le(690)];afternoon=g[t.ge(780)];total=g.volume.sum();changes=np.log(g.close/g.close.shift());path=changes.abs().sum();hit=g[g.high.ge(cap-.005)];seal=g.close.ge(cap-.005);out.update({'首30分钟涨幅':first.close.iloc[-1]/g.open.iloc[0]-1,'末30分钟涨幅':g.close.iloc[-1]/last.open.iloc[0]-1,'上午涨幅':morning.close.iloc[-1]/g.open.iloc[0]-1,'下午涨幅':g.close.iloc[-1]/afternoon.open.iloc[0]-1,'首30分钟成交量占比':first.volume.sum()/total,'末30分钟成交量占比':last.volume.sum()/total,'下午成交量占比':afternoon.volume.sum()/total,'下午相对上午成交量':afternoon.volume.sum()/morning.volume.sum() if morning.volume.sum()>0 else np.nan,'上涨K成交量方向比':(np.sign(g.close-g.open)*g.volume).sum()/total,'上涨分钟比例':g.close.gt(g.open).mean(),'全日分钟波动':np.sqrt((changes**2).sum()),'走势效率':abs(np.log(g.close.iloc[-1]/g.open.iloc[0]))/path if path>0 else np.nan,'触板时间':int(hit.minute.iloc[0])-570 if len(hit) else np.nan,'收板分钟占比':seal.mean(),'分钟收盘开板次数':(g.close.shift().ge(cap-.005)&~seal).sum(),'封板时成交量占比':g.loc[seal,'volume'].sum()/total,'尾盘板下分钟占比':last.close.lt(cap-.005).sum()/30})
 for n in names:out.setdefault(n,np.nan)
 return out
with ThreadPoolExecutor(max_workers=6) as ex:out=list(ex.map(fetch,x.to_dict('records')))
z=pd.DataFrame(out);z.to_pickle(P/'十三日分时因子.pkl.gz',compression='gzip');z[['qdc_symbol','d0_date','分钟数','分时有效','来源SHA256']].to_csv(P/'十三日分时审计.csv',index=False);print('候选',len(x),'分时有效',z.分时有效.sum(),flush=True)
