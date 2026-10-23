from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import sys, json, hashlib, math
import numpy as np
import pandas as pd

ROOT=Path('/data/cyris/work/b2_board_volume_r71_20261022')
BASE=Path('/data/cyris/work/b2_d2_limit_follow_r69_20261021')
R67=Path('/data/cyris/work/b2_d2_intraday_r67_20261021/inputs')
sys.path.insert(0,'/data/cyris/fin_agent_deploy/b2-20261015')
from scripts.benchmark_automl_1440_inference import db_connection,query

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

s=pd.read_pickle(BASE/'all_signals.pkl').sort_values(['d0_date','stk_code']).reset_index(drop=True)
d=pd.read_pickle(BASE/'daily.pkl').set_index(['trade_date','stk_code'])
cal=json.loads((R67/'calendar.json').read_text())
ix={x:i for i,x in enumerate(cal)}
assert len(s)==709 and int(s.v2_allowed.sum())==306
board_pairs=set()
for r in s.itertuples():
    a,b=ix[r.first_trade_date],ix[r.d0_date]
    assert b-a+1==r.board_streak_days,(r.stk_code,r.d0_date,a,b,r.board_streak_days)
    board_pairs.update((day,r.stk_code) for day in cal[a:b+1])
board_pairs=sorted(board_pairs)
with db_connection('/home/che/cyris/fin_agent/.env') as db:
    chunks=[]
    for i in range(0,len(board_pairs),350):
        part=board_pairs[i:i+350]
        sql='SELECT trade_date,stk_code,preclose,open,high,low,close,volume,amount,turn_ratio,is_limit_price FROM kcrp_stock_price WHERE (trade_date,stk_code) IN ('+','.join(['(%s,%s)']*len(part))+')'
        chunks.append(query(db,sql,tuple(v for pair in part for v in pair)))
    board=pd.concat(chunks,ignore_index=True)
    assert len(board)==len(board_pairs),(len(board),len(board_pairs))
    pre={}
    unique=sorted(set(zip(s.stk_code,s.first_trade_date)))
    for j,(code,first) in enumerate(unique):
        sql='SELECT trade_date,stk_code,volume,amount,turn_ratio,close,is_limit_price FROM kcrp_stock_price WHERE stk_code=%s AND trade_date<%s AND volume>0 ORDER BY trade_date DESC LIMIT 20'
        pre[(code,first)]=query(db,sql,(code,first))
    db.rollback()
board['trade_date']=board.trade_date.astype(str)
board.sort_values(['stk_code','trade_date']).to_csv(ROOT/'board_daily_source.csv.gz',index=False)
preframes=[]
for (code,first),frame in pre.items():
    if frame.empty: continue
    q=frame.copy();q['对应首板日']=first;preframes.append(q)
pd.concat(preframes,ignore_index=True).sort_values(['stk_code','对应首板日','trade_date']).to_csv(ROOT/'preboard_daily_source.csv.gz',index=False)
board=board.set_index(['trade_date','stk_code'])
for c in ['volume','amount','turn_ratio','close','is_limit_price']:
    board[c]=pd.to_numeric(board[c])
dayrows=[]
for r in s.itertuples():
    a,b=ix[r.first_trade_date],ix[r.d0_date]
    seq=board.loc[[(day,r.stk_code) for day in cal[a:b+1]]].reset_index()
    assert len(seq)==r.board_streak_days
    assert all(seq.is_limit_price.astype(int).eq(1)),(r.stk_code,r.d0_date,seq[['trade_date','is_limit_price']].to_dict('records'))
    assert abs(seq.iloc[0].volume-r.first_volume)<1 and abs(seq.iloc[-1].volume-r.volume)<1
    p=pre[(r.stk_code,r.first_trade_date)].copy()
    if p.empty:p=pd.DataFrame(columns=['volume','amount','turn_ratio'])
    for c in ['volume','amount','turn_ratio']:p[c]=pd.to_numeric(p[c])
    vv=seq.volume.to_numpy(float)
    vprev=p.volume.to_numpy(float)
    out={'股票':r.stk_code,'D0选中日':r.d0_date,'D1买入日':r.d1_date,'D2原卖出日':r.d2_date,'V2放行':int(bool(r.v2_allowed)),
         'D0排名':int(r.d0_rank),'D0分数':float(r.score),'已连板天数':int(r.board_streak_days),'首板日':r.first_trade_date,
         '板前实际交易日数':len(p),'选股日成交量_万股':r.volume/1e4,'首板成交量_万股':r.first_volume/1e4,
         '首板一字':int(r.first_one_price),'D0一字':int(r.d0_one_price),'D0换手率_pct':float(r.turn_ratio),
         '首板换手率_pct':float(r.first_turn_ratio),
         'D0成交额_亿元':float(r.amount)/1e8,
         '全部连板日期成交量':';'.join(f'{x.trade_date}:{x.volume/1e4:.4f}' for x in seq.itertuples())}
    for k in [2,3,4]:
        out[f'第{k}板成交量_万股']=vv[k-1]/1e4 if len(vv)>=k else np.nan
    for k in [1,2,3,4,5]:
        out[f'倒数第{k}板成交量_万股']=vv[-k]/1e4 if len(vv)>=k else np.nan
    for n in [5,10,20]:
        x=vprev[:n] if len(vprev)>=n else np.array([])
        for stat in ['均','最大','最小']:
            key=f'首板前{n}日{stat}量_万股'
            out[key]=(float({'均':np.mean,'最大':np.max,'最小':np.min}[stat](x))/1e4) if len(x) else np.nan
        out[f'首板量除前{n}日均量']=vv[0]/np.mean(x) if len(x) and np.mean(x)>0 else np.nan
        out[f'D0量除首板前{n}日均量']=vv[-1]/np.mean(x) if len(x) and np.mean(x)>0 else np.nan
    out['二板量除首板量']=vv[1]/vv[0] if len(vv)>=2 and vv[0]>0 else np.nan
    out['三板量除二板量']=vv[2]/vv[1] if len(vv)>=3 and vv[1]>0 else np.nan
    out['四板量除三板量']=vv[3]/vv[2] if len(vv)>=4 and vv[2]>0 else np.nan
    out['D0量除前一板量']=vv[-1]/vv[-2] if len(vv)>=2 and vv[-2]>0 else np.nan
    out['D0量除首板量']=vv[-1]/vv[0] if vv[0]>0 else np.nan
    out['连板段最高量除最低量']=max(vv)/min(vv) if min(vv)>0 else np.nan
    out['连板段最低量板序']=int(np.argmin(vv)+1)
    out['二板缩量']=int(vv[1]<vv[0]) if len(vv)>=2 else np.nan
    out['三板放量']=int(vv[2]>vv[1]) if len(vv)>=3 else np.nan
    out['四板放量']=int(vv[3]>vv[2]) if len(vv)>=4 else np.nan
    out['先缩后二板三板放量']=int(vv[1]<vv[0] and vv[2]>vv[1]) if len(vv)>=3 else np.nan
    out['首板量低于前20日均量']=int(vv[0]<np.mean(vprev[:20])) if len(vprev)>=20 else np.nan
    d2=d.loc[(r.d2_date,r.stk_code)]
    down_rate=.2 if r.symbol.startswith(('30','68')) else (.05 if r.d2_st in ('S','Y') else .1)
    down=float((Decimal(str(d2.preclose))*(1-Decimal(str(down_rate)))).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
    open_floor=abs(float(d2.open)-down)<.001
    close_floor=int(d2.is_limit_price)==2
    assert close_floor==(abs(float(d2.close)-down)<.001)
    d3=d.loc[(r.d3_date,r.stk_code)] if (r.d3_date,r.stk_code) in d.index else None
    out.update({'D2开盘跌停':int(open_floor),'D2收盘跌停':int(close_floor),'D2开收跌停并集':int(open_floor or close_floor),
        'D2D3连续收盘跌停':int(close_floor and d3 is not None and int(d3.is_limit_price)==2),
        'D2收盘涨跌幅_pct':(float(d2.close)/float(d2.preclose)-1)*100})
    dayrows.append(out)
t=pd.DataFrame(dayrows)
assert int(t.loc[t.V2放行.eq(1),'D2开收跌停并集'].sum())==67
assert int(t.loc[t.V2放行.eq(1),'D2开盘跌停'].sum())==34
assert int(t.loc[t.V2放行.eq(1),'D2收盘跌停'].sum())==52
assert int(t.loc[t.V2放行.eq(1),'D2D3连续收盘跌停'].sum())==20
assert t[['股票','D0选中日']].duplicated().sum()==0
t.to_csv(ROOT/'B2_候选连板量能特征_709笔.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
t[t.V2放行.eq(1)].to_csv(ROOT/'B2_V2放行连板量能特征_306笔.csv',index=False,encoding='utf-8-sig',float_format='%.6f')
summary={'source':{'signals':str(BASE/'all_signals.pkl'),'signals_sha256':sha(BASE/'all_signals.pkl'),'daily':str(BASE/'daily.pkl'),'daily_sha256':sha(BASE/'daily.pkl'),'calendar':str(R67/'calendar.json'),'calendar_sha256':sha(R67/'calendar.json'),'preboard_and_board':'KingdomAI kcrp_stock_price read-only at build time'},
         'counts':{'all_candidates':len(t),'v2_allowed':int(t.V2放行.sum()),'preboard20_complete':int(t['板前实际交易日数'].eq(20).sum()),'max_board_streak':int(t['已连板天数'].max())},
         'v2_outcomes':{c:int(t.loc[t.V2放行.eq(1),c].sum()) for c in ['D2开盘跌停','D2收盘跌停','D2开收跌停并集','D2D3连续收盘跌停']},
         'files':{}}
for fn in ['B2_候选连板量能特征_709笔.csv','B2_V2放行连板量能特征_306笔.csv','board_daily_source.csv.gz','preboard_daily_source.csv.gz']:
 summary['files'][fn]=sha(ROOT/fn)
(ROOT/'manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
