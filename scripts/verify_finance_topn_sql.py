"""Run existing evaluation queries and SQL-equivalence checks against candidate modules."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def main():
    p=argparse.ArgumentParser();p.add_argument('--candidate-dir',type=Path,required=True);a=p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    from src.utils.mysql_utils import StockInfoDbUtils
    from src.experiments.staged_data_protocol.phase2 import quote_provider as oldq, intraday_quote_provider as oldm
    if (a.candidate_dir/'provider_backup/quote_provider.py').exists():
        oldq=load('baseline_daily',a.candidate_dir/'provider_backup/quote_provider.py')
        oldm=load('baseline_minute',a.candidate_dir/'provider_backup/intraday_quote_provider.py')
    import pymysql
    class BoundedDB(StockInfoDbUtils):
        def connect_db(self):
            super().connect_db()
            with self.conn.cursor() as c: c.execute('SET SESSION MAX_EXECUTION_TIME=60000')
    q=load('candidate_daily',a.candidate_dir/'quote_provider.py');m=load('candidate_minute',a.candidate_dir/'intraday_quote_provider.py')
    q.StockInfoDbUtils=BoundedDB;m.StockInfoDbUtils=BoundedDB
    report={'queries':{},'equivalence':[]}
    cases=[('BUS008',q,{'mode':0,'count':1,'order':'pct desc','limit':20},['code','name','tradedate','pct','close']),
        ('BUS010',q,{'mode':0,'count':10,'filter':'code = 300750.SZ'},['code','name','tradedate','close','pct']),
        ('BUS011',q,{'mode':0,'count':20},['code','name','tradedate','high','low','preclose','amplitude']),
        ('BUS012',m,{'mode':1,'period':1,'order':'volumn desc','limit':20},['code','name','bar_start_time','bar_end_time','volumn','amount','is_finalized'])]
    for cid,mod,args,outputs in cases:
        started=time.monotonic()
        r=mod.execute_quote_api(subject='stock',args=args,outputs=outputs) if mod is q else mod.execute_intraday_quote_api(args=args,outputs=outputs)
        rows=r.get('rows',[])
        report['queries'][cid]={'status':r['status'],'ms':round((time.monotonic()-started)*1000,3),'rows':len(rows),'reason':r.get('reason'),'sample':rows[:2]}
        print(cid,report['queries'][cid],flush=True)
    # Same SQL predicates, per-code count and final sort, comparing whole ordered rows.
    d=BoundedDB(database='kingdomai')
    try:
        fields=['code','name','tradedate','close','pct'];source=q.QUOTE_SOURCES['stock']
        for args in [{'codes':['600519.SH'],'count':1}, {'codes':['600519.SH','300750.SZ'],'count':10},
            {'filter':'code in (600519.SH,300750.SZ) and pct > 0','count':3,'order':'pct desc'},
            {'codes':['600519.SH','300750.SZ'],'start':'2025-01-01','end':'2025-01-31','count':5}]:
            where,params=q._build_where(source=source,args=args)
            results=[]
            for mod in [oldq,q]:
                sql=mod._build_per_entity_sql(source=source,fields=fields,where_sql=where,args=args)
                with d.conn.cursor(pymysql.cursors.DictCursor) as c:
                    scope_params=mod._count_identity_scope(source=source,args=args)[1] if mod is q else []
                    c.execute(sql,[*scope_params,*params,args['count'],500001]);results.append(c.fetchall())
            check={'kind':'daily','args':args,'equal':results[0]==results[1],'rows':len(results[1])}
            report['equivalence'].append(check);print(check,flush=True)
        # Native-bar identity/date predicates are applied before selecting recent keys.
        oldm.StockInfoDbUtils=BoundedDB
        for args in [{'mode':1,'period':60,'count':3,'filter':'code = 600519.SH'},
                     {'mode':1,'period':1,'count':2,'filter':'code = 600519.SH and tradedate <= 2026-09-04'}]:
            outputs=['code','tradedate','bar_end_time','open','close','volumn']
            x=oldm.execute_intraday_quote_api(args=args,outputs=outputs);y=m.execute_intraday_quote_api(args=args,outputs=outputs)
            check={'kind':'minute','args':args,'equal':x['status']=='ok' and y['status']=='ok' and x['rows']==y['rows'],'rows':len(y['rows']),'old_status':x['status'],'new_status':y['status']}
            report['equivalence'].append(check);print(check,flush=True)
    finally:
        d.close_db();(a.candidate_dir/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
    if any(r['status']!='ok' for r in report['queries'].values()) or not all(r['equal'] for r in report['equivalence']):raise SystemExit(2)

if __name__=='__main__':main()
