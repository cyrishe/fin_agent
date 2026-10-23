from pathlib import Path
from collections import defaultdict
import hashlib, json, subprocess
import numpy as np
import pandas as pd
ROOT=Path('/data/cyris/work/b2_d2_open_volume_ranks_r81_20261022')
R80=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
R79=Path('/data/cyris/work/b2_d2_floor_volume_r79_20261022')
R76=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
cases=pd.read_csv(ROOT/'09_31仍封死19笔_核心变量与结局.csv',dtype={'股票':str})
daily=pd.read_csv(R80/'preboard_daily_selected.csv.gz',dtype={'stk_code':str})
trade=pd.read_csv(R79/'100笔D2触跌停_成交量逐笔.csv',dtype={'股票':str})
source=json.loads((R80/'首板前压缩源清单.json').read_text())
paths={z['date']:z['path'] for z in source}
minute_source={z['id']:z['source'] for z in json.loads((R76/'minute_source_manifest.json').read_text())}
wanted=defaultdict(set)
history={}
for _,r in cases.iterrows():
    h=daily[(daily.stk_code==r['股票'])&(daily['对应首板日']==trade.loc[
      (trade['股票']==r['股票'])&(trade['D0选中日']==r['D0选中日']),'首板日'].iloc[0])].sort_values('trade_date',ascending=False)
    days=h.head(20).trade_date.tolist()
    board=trade.loc[(trade['股票']==r['股票'])&(trade['D0选中日']==r['D0选中日']),'首板日'].iloc[0]
    assert len(days)==20 and len(set(days))==20 and all(d<board for d in days)
    key=(r['股票'],r['D0选中日'])
    history[key]=days
    for day in days:wanted[day].add(r['股票'])
official=daily.set_index(['trade_date','stk_code'])['volume']
baseline={}
for i,day in enumerate(sorted(wanted),1):
    path=Path(paths[day]);codes=wanted[day]
    dec=subprocess.Popen(['gzip','-dc',str(path)] if path.suffix=='.gz' else ['zstdcat',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    args=['grep','-F']
    for code in sorted(codes):args+=['-e',code]
    filt=subprocess.Popen(args,stdin=dec.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    dec.stdout.close()
    payload,err=filt.communicate()
    dec_err=dec.stderr.read()
    assert dec.wait()==0,(path,dec_err[-200:])
    assert filt.returncode==0,(path,err[-200:])
    rows=defaultdict(list)
    tsh=path.suffix=='.gz'
    for line in payload.splitlines():
        a=line.decode().split(',')
        code=a[0] if tsh else a[1]
        if code not in codes:continue
        stamp=a[1] if tsh else a[0]
        assert stamp[:10]==day
        rows[code].append((stamp[11:16],float(a[6])))
    for code in codes:
        bars=rows[code]
        assert len(bars)==(241 if tsh else 240),(day,code,len(bars))
        opening=sum(v for clock,v in bars if clock in (('09:30','09:31') if tsh else ('09:31',)))
        total=sum(v for _,v in bars)
        known=float(official.loc[(day,code)])
        assert abs(total-known)<=max(5000,.0001*known),(day,code,total,known)
        baseline[(day,code)]=opening
    if i%50==0:print('audited preboard archives',i,'/',len(wanted),flush=True)
result=[]
for _,r in cases.iterrows():
    code=r['股票'];d0=r['D0选中日'];day=r['D2跌停日']
    info=minute_source[code+'|'+d0]
    q=Path(info['path'])
    assert q.suffix=='.gz' and hashlib.sha256(q.read_bytes()).hexdigest()==info['sha256']
    m=pd.read_csv(q,dtype={'symbol':str})
    assert len(m)==240 and m.symbol.eq(code).all() and m.time.str[:10].eq(day).all()
    m=m.sort_values('time').reset_index(drop=True)
    t=trade[(trade['股票']==code)&(trade['D0选中日']==d0)].iloc[0]
    floor=float(t['D2跌停价'])
    raw_open=float(m.open.iloc[0]);raw_high=float(m.high.iloc[0])
    raw_one=int((m[['open','high','low','close']].to_numpy(float)<=floor+.001).all())
    raw_later_full=int((m.low.iloc[1:].to_numpy(float)>floor+.001).any())
    assert abs(raw_open-floor)<=.001 and raw_high<=floor+.001,(code,day)
    assert raw_one==int(r['D2全天一价封死']) and raw_later_full==int(r['首触后曾有后续完整分钟离板']),(code,day)
    vol=float(m.volume.iloc[0])
    assert np.isclose(vol,float(r['开盘首分钟量_股']),atol=.5)
    days=history[(code,d0)]
    hist=np.array([baseline[(d,code)] for d in days],float)
    score=vol/hist.max()
    assert np.isclose(score,float(r['首分钟量除板前20日同刻最大']),rtol=1e-7,atol=1e-7),(code,day,score,r['首分钟量除板前20日同刻最大'])
    result.append({'股票':code,'D0选中日':d0,'D2日':day,'首板日':t['首板日'],
      '原始D2首分钟成交股数':vol,'板前20日同刻最大股数':hist.max(),
      '独立重算F05':score,'原始分钟全天一价封死bad':raw_one,
      '原始分钟后续整分钟离板good':raw_later_full,'D2分钟SHA256':info['sha256']})
out=pd.DataFrame(result).sort_values(['独立重算F05','D2日','股票']).reset_index(drop=True)
out['排序分段']='中段9'
out.loc[out.index[:5],'排序分段']='低端5'
out.loc[out.index[-5:],'排序分段']='高端5'
assert len(out)==19 and out['原始分钟后续整分钟离板good'].sum()==6 and out['原始分钟全天一价封死bad'].sum()==13
bins=out.groupby('排序分段').agg(笔数=('股票','size'),good=('原始分钟后续整分钟离板good','sum'),bad=('原始分钟全天一价封死bad','sum')).reindex(['低端5','中段9','高端5'])
assert bins[['good','bad']].to_numpy().tolist()==[[0,5],[2,7],[4,1]]
out.to_csv(ROOT/'原始分钟独立复算19笔.csv',index=False,encoding='utf-8-sig',float_format='%.8f')
def auc(z):
    pos=z.loc[z['原始分钟后续整分钟离板good'].eq(1),'独立重算F05'].to_numpy(float)
    neg=z.loc[z['原始分钟后续整分钟离板good'].eq(0),'独立重算F05'].to_numpy(float)
    if not len(pos) or not len(neg):return None
    return float(((pos[:,None]>neg[None,:]).sum()+.5*(pos[:,None]==neg[None,:]).sum())/(len(pos)*len(neg)))
year=[]
for yr,g in out.groupby(out['D2日'].str[:4]):
    year.append({'年份':yr,'笔数':len(g),'good':int(g['原始分钟后续整分钟离板good'].sum()),
                 'bad':int(g['原始分钟全天一价封死bad'].sum()),'F05_AUC':auc(g)})
pd.DataFrame(year).to_csv(ROOT/'F05逐年独立复核.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
dedup=out.sort_values('D2日').drop_duplicates('股票',keep='first')
audit={'source_level_d2_files':len(out),'source_level_preboard_stock_days':len(baseline),
       'source_level_preboard_archives':len(wanted),'source_level_daily_volume_checked':True,
       'F05_low_middle_high_good_bad':bins[['good','bad']].to_numpy().tolist(),
       'F05_full_AUC':auc(out),'unique_stock_n':len(dedup),'unique_stock_AUC_earliest_D2':auc(dedup),
       'yearly':year}
(ROOT/'raw_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(audit,ensure_ascii=False),flush=True)
