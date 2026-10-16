"""Read-only 100-stock sample and auditable monthly pattern prevalence.

Select without consulting pattern outcomes. Preserve a frozen snapshot for replay.
"""
from __future__ import annotations
import argparse, contextlib, gzip, hashlib, io, json, platform, shutil, subprocess, time
from pathlib import Path
import numpy as np
import pandas as pd
from src.experiments.kline_patterns.catalog import CATALOG
from src.experiments.kline_patterns.engine import prepare, detect
from src.experiments.kline_patterns.run import clean

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/development_tasks/evidence/kline_frequency_100_20260929'
ASOF='2026-09-28'
MONTH_START='2026-08-29'
SEED='kline-frequency-100-20260929'
QUOTAS={'SH_main':35,'SZ_main':30,'ChiNext':20,'STAR':10,'BJ':5}


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False))


def board(code):
    if code.endswith('.BJ'):return 'BJ'
    if code.endswith('.SH') and code.startswith('60'):return 'SH_main'
    if code.endswith('.SH') and code.startswith('68'):return 'STAR'
    if code.endswith('.SZ') and code.startswith('00'):return 'SZ_main'
    if code.endswith('.SZ') and code.startswith('30'):return 'ChiNext'
    return None


def fetch():
    from dotenv import load_dotenv
    from src.experiments.kline_patterns.fetch import ReadOnlyDB
    load_dotenv(ROOT/'.env',override=False)
    universe=json.loads((OUT/'universe.json').read_text())
    strata=[]
    for name,quota in QUOTAS.items():
        pool=sorted([r for r in universe if board(r['stk_code'])==name and r.get('amount',0)>0],key=lambda r:(r['amount'],r['stk_code']))
        for k,indices in enumerate(np.array_split(np.arange(len(pool)),5),1):
            members=sorted([pool[int(i)] for i in indices],key=lambda r:hashlib.sha256((SEED+r['stk_code']).encode()).hexdigest())
            strata.append({'board':name,'turnover_quintile':k,'quota':quota//5,'candidates':[r['stk_code'] for r in members]})
    dump(OUT/'sampling_plan.json',{'seed':SEED,'asof':ASOF,'month_start':MONTH_START,'board_quotas':QUOTAS,'strata':strata,'eligibility':'Active at asof; >=260 finite valid positive-volume daily bars since 2024-01-01; >=15 valid bars in target month. Candidates selected by SHA256 within board/turnover quintile, before any pattern scan. Replacements remain in same stratum.'})
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):db=ReadOnlyDB(database='kingdomai')
    rows=[];selected=[];rejected=[]
    started=time.perf_counter()
    try:
        with db.conn.cursor() as cur:
            cur.execute('SET SESSION MAX_EXECUTION_TIME=10000')
            cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            for stratum in strata:
                count=0
                for code in stratum['candidates']:
                    cur.execute('SELECT trade_date,stk_code,open,high,low,close,adjopen,adjhigh,adjlow,adjclose,volume,amount,turn_ratio FROM kcrp_stock_price WHERE stk_code=%s AND trade_date>=%s AND trade_date<=%s ORDER BY trade_date LIMIT 1000',(code,'2024-01-01',ASOF))
                    part=[{k:str(v) if k in ('trade_date','stk_code') else None if v is None else float(v) for k,v in r.items()} for r in cur.fetchall()]
                    b=pd.DataFrame(part)
                    if b.empty:rejected.append({'code':code,'reason':'no rows'});continue
                    cols=['adjopen','adjhigh','adjlow','adjclose','volume']
                    valid=np.isfinite(b[cols]).all(axis=1)&(b[cols]>0).all(axis=1)
                    valid&=(b.adjhigh>=b[['adjopen','adjlow','adjclose']].max(axis=1))&(b.adjlow<=b[['adjopen','adjhigh','adjclose']].min(axis=1))
                    month=(b.trade_date>=MONTH_START)&(b.trade_date<=ASOF)
                    if valid.sum()<260 or (valid&month).sum()<15:
                        rejected.append({'code':code,'reason':'insufficient valid history','valid':int(valid.sum()),'month_valid':int((valid&month).sum())});continue
                    rows.extend(part);selected.append({'code':code,'board':stratum['board'],'turnover_quintile':stratum['turnover_quintile'],'rows':len(part),'first':part[0]['trade_date'],'last':part[-1]['trade_date'],'month_rows':int(month.sum()),'month_valid':int((valid&month).sum())});count+=1
                    if len(selected)%10==0:print('fetched',len(selected),'stocks',flush=True)
                    if count==stratum['quota']:break
                assert count==stratum['quota'],stratum['board']
    finally:
        db.conn.rollback()
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):db.close_db()
    assert len(selected)==100
    data=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode()
    with gzip.GzipFile(filename=str(OUT/'daily_inputs.jsonl.gz'),mode='wb',mtime=0) as f:f.write(data)
    dump(OUT/'input_manifest.json',{'source':'read-only kingdomai.kcrp_stock_price','asof':ASOF,'fetch_seconds':time.perf_counter()-started,'data_rows':len(rows),'sha256_uncompressed':hashlib.sha256(data).hexdigest(),'selection':selected,'rejected':rejected,'sampling_plan_sha256':hashlib.sha256((OUT/'sampling_plan.json').read_bytes()).hexdigest()})
    print('saved',len(rows),'rows',flush=True)


def frames():
    raw=pd.DataFrame([json.loads(s) for s in gzip.open(OUT/'daily_inputs.jsonl.gz','rt')]);raw.trade_date=pd.to_datetime(raw.trade_date)
    for code,b in raw.groupby('stk_code',sort=True):
        b=b.set_index('trade_date').sort_index();scale=b.close.iloc[-1]/b.adjclose.iloc[-1]
        b=b.drop(columns=['open','high','low','close']).rename(columns={f'adj{k}':k for k in ['open','high','low','close']})
        b[['open','high','low','close']]*=scale
        yield code,prepare(b)


def scan(label):
    out=OUT/label;out.mkdir(exist_ok=False)
    definitions=json.loads(json.dumps(CATALOG,ensure_ascii=False));dump(out/'definitions.json',definitions)
    windows=[('month',MONTH_START,ASOF),('previous_month','2026-07-29','2026-08-28'),('two_months_ago','2026-06-29','2026-07-28')]
    counts={(name,p['id']):[] for name,_,_ in windows for p in definitions};hits=[];quality=[]
    board_map={r['code']:r['board'] for r in json.loads((OUT/'input_manifest.json').read_text())['selection']}
    started=time.perf_counter()
    for number,(code,f) in enumerate(frames(),1):
        quality.append({'code':code,'rows':len(f),'month_bars':len(f.loc[MONTH_START:ASOF]),'month_valid':int(f.loc[MONTH_START:ASOF].valid.sum())})
        for p in definitions:
            detected=detect(p,f);yes=detected.fillna(False).astype(bool)
            # A continuous state can occupy multiple days. Count episode starts too,
            # including the case where the state began before the requested month.
            episode_start=yes & ~yes.shift(1,fill_value=False)
            for name,lo,hi in windows:
                s=detected.loc[lo:hi];trigger=yes.loc[lo:hi];episodes=episode_start.loc[lo:hi]
                counts[(name,p['id'])].append({'code':code,'board':board_map[code],'days':len(s),'known':int(s.notna().sum()),'hits':int(trigger.sum()),'episode_starts':int(episodes.sum()),'present':bool(trigger.any())})
                if name=='month':
                    for date in trigger.index[trigger]:
                        at=f.index.get_loc(date);r=f.iloc[at];prev=f.iloc[at-1]
                        hits.append({'pattern':p['id'],'code':code,'date':str(date.date()),'episode_start':bool(episode_start.loc[date]),'body_to_prior20_body_mean':r.body/r.body_avg,'body_to_prior_atr':r.body/prev.atr,'previous_body_range_ratio':prev.body/prev['range'] if prev['range']>0 else None,'volume_ratio':r.v/r.vbase,'prior5_direction_atr':(f.c.iloc[at-2]-f.c.iloc[at-7])/prev.atr})
        if number%20==0:print(label,'scanned',number,flush=True)
    stats=[]
    for (window,pid),rows in counts.items():
        p=next(p for p in definitions if p['id']==pid)
        total=sum(r['hits'] for r in rows);stocks=sum(r['present'] for r in rows);known=sum(r['known'] for r in rows)
        stats.append({'window':window,'id':pid,'name':p['name'],'family':p['family'],'revision':p['revision'],'hit_stocks':stocks,'coverage_pct':stocks,'hit_days':total,'episode_starts':sum(r['episode_starts'] for r in rows),'known_stock_days':known,'unknown_stock_days':sum(r['days']-r['known'] for r in rows),'hit_pct_of_known_days':100*total/known if known else None,'hits_per_hit_stock':total/stocks if stocks else 0,'by_board':{b:sum(r['present'] for r in rows if r['board']==b) for b in QUOTAS}})
    stats.sort(key=lambda r:(r['window'],-r['hit_stocks'],-r['hit_days'],r['id']))
    dump(out/'frequency.json',stats);dump(out/'per_stock_counts.json',[{'window':w,'pattern':p,**row} for (w,p),rs in counts.items() for row in rs]);dump(out/'hits.json',hits);dump(out/'quality.json',quality)
    sources=[Path(__file__),ROOT/'src/experiments/kline_patterns/catalog.py',ROOT/'src/experiments/kline_patterns/engine.py',ROOT/'src/experiments/kline_patterns/analysis.py',ROOT/'src/services/technical_indicator_calculator.py']
    for p in sources:
        target=out/'source_snapshot'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    dump(out/'manifest.json',{'started_asof':ASOF,'windows':windows,'stocks':100,'definitions':len(definitions),'seconds':time.perf_counter()-started,'python':platform.python_version(),'pandas':pd.__version__,'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'file_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'input_sha256':hashlib.sha256((OUT/'daily_inputs.jsonl.gz').read_bytes()).hexdigest(),'model_calls':0,'measurement':'Coverage counts each stock once per month. Also report stock-day prevalence and consecutive episode starts, not only raw repeated state days. Prior months provide regime context, not profitability.'})
    print(json.dumps([{'id':r['id'],'stocks':r['hit_stocks'],'days':r['hit_days']} for r in stats if r['window']=='month'],ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true');parser.add_argument('--scan',action='store_true');parser.add_argument('--label',default='baseline');args=parser.parse_args()
    if args.fetch:fetch()
    if args.scan:scan(args.label)
