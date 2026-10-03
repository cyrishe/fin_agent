"""Bounded read-only old/new top-N query-plan comparison on the configured database."""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    from src.experiments.staged_data_protocol.phase2 import quote_provider as q, intraday_quote_provider as m
    from src.utils.mysql_utils import StockInfoDbUtils
    import pymysql
    d=StockInfoDbUtils(database='kingdomai')
    source=q.QUOTE_SOURCES['stock']
    report={}
    try:
        with d.conn.cursor(pymysql.cursors.DictCursor) as c:
            c.execute('SET SESSION MAX_EXECUTION_TIME=5000')
            c.execute('SET SESSION lock_wait_timeout=3')
            fields=['code','name','tradedate','close','pct']
            for label,a in [('one',{'codes':['600519.SH'],'count':1}),('all1',{'count':1,'order':'pct desc','limit':20}),('all20',{'count':20})]:
                where,params=q._build_where(source=source,args=a)
                old=q._build_per_entity_sql(source=source,fields=fields,where_sql=where,args=a)
                select=', '.join(f'{source.fields[f]} AS `{f}`' for f in fields)
                new=f'''SELECT recent.* FROM (SELECT DISTINCT stk_code FROM kcrp_stock_price) ids
                    JOIN LATERAL (SELECT {select} FROM kcrp_stock_price q
                    LEFT JOIN kcrp_stock_baseinfo b ON b.stk_code=q.stk_code
                    WHERE q.stk_code=ids.stk_code AND {where}
                    ORDER BY q.stk_code DESC,q.trade_date DESC LIMIT %s) recent ON TRUE
                    ORDER BY {'recent.pct DESC' if label=='all1' else 'recent.code, recent.tradedate DESC'} LIMIT %s'''
                for variant,sql in [('candidate',new)]:
                    bindings=[*params,a['count'],20 if label=='all1' else 500001]
                    probe(c,report,label+'_'+variant,sql,bindings)
            base=m._stored_bar_cte(kline_type='1m',period_minutes=1)
            minute_old=f'''{base},recent AS (SELECT base.*,ROW_NUMBER() OVER(PARTITION BY code ORDER BY tradedate DESC,bar_end_time DESC,snapshot_time DESC) rn FROM base)
                SELECT code,name,tradedate,bar_end_time,volumn FROM recent WHERE rn<=%s ORDER BY volumn DESC LIMIT %s'''
            minute_new=f'''{base} SELECT recent.code,recent.name,recent.tradedate,recent.bar_end_time,recent.volumn
                FROM (SELECT DISTINCT stk_code FROM {m.SNAPSHOT_TABLE} WHERE stk_code REGEXP '^[0-9]{{6}}$') ids
                JOIN LATERAL (SELECT base.* FROM base WHERE base.code=ids.stk_code ORDER BY code DESC,kline_type DESC,tradedate DESC,bar_end_time DESC LIMIT %s) recent ON TRUE
                ORDER BY volumn DESC LIMIT %s'''
            probe(c,report,'minute_candidate',minute_new,[20,20])
            c.execute('SET SESSION MAX_EXECUTION_TIME=15000')
            keys=f'''SELECT s.stk_code,s.kline_type,s.trade_date,s.bar_end_time FROM {m.SNAPSHOT_TABLE} s
                WHERE s.stk_code=ids.stk_code AND s.kline_type='1m' AND s.period_minutes=1
                ORDER BY s.stk_code DESC,s.kline_type DESC,s.trade_date DESC,s.bar_end_time DESC LIMIT %s'''
            keyed=f'''SELECT s.stk_code,s.stk_name,s.trade_date,s.bar_end_time,s.volume
                FROM (SELECT DISTINCT stk_code FROM {m.SNAPSHOT_TABLE} WHERE stk_code REGEXP '^[0-9]{{6}}$') ids
                JOIN LATERAL ({keys}) recent ON TRUE
                JOIN {m.SNAPSHOT_TABLE} s ON s.trade_date=recent.trade_date AND s.stk_code=recent.stk_code AND s.kline_type=recent.kline_type AND s.bar_end_time=recent.bar_end_time
                ORDER BY s.volume DESC LIMIT %s'''
            probe(c,report,'minute_key_candidate',keyed,[20,20])
    finally:
        d.close_db();Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))

def probe(c,report,label,sql,bindings):
    r={'sql':c.mogrify(sql,bindings)};report[label]=r
    c.execute('EXPLAIN '+sql,bindings);r['explain']=c.fetchall()
    started=time.monotonic()
    try:
        c.execute(sql,bindings);rows=c.fetchall();r['row_count']=len(rows);r['sample']=rows[:3]
    except Exception as e: r['error']=str(e)
    r['ms']=round((time.monotonic()-started)*1000,3)
    print(label,r['ms'],r.get('row_count'),r.get('error',''),flush=True)

if __name__=='__main__':main()
