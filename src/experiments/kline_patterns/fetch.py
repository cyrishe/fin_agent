"""Bounded read-only OHLCV fetch, using existing configured market DB credentials."""
import contextlib,gzip,hashlib,io,json
from datetime import datetime, timezone
from pathlib import Path
import pymysql
from dotenv import load_dotenv
from src.utils.mysql_utils import StockInfoDbUtils
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/research/kline_pattern_library_20260928'
# Chosen before looking for any pattern matches; diverse sectors, not a representative market sample.
CODES=['000001.SZ','000002.SZ','000063.SZ','000333.SZ','000651.SZ','000725.SZ','000858.SZ','002027.SZ','002230.SZ','002415.SZ','002594.SZ','300059.SZ','300124.SZ','300750.SZ','600000.SH','600030.SH','600036.SH','600031.SH','600276.SH','600519.SH','600900.SH','601318.SH','601899.SH','688981.SH']
class ReadOnlyDB(StockInfoDbUtils):
 def connect_db(self):
  self.conn=pymysql.connect(host=self.host,user=self.user,password=self.password,database=self.database,port=self.port,charset='utf8mb4',connect_timeout=5,read_timeout=15,write_timeout=10,cursorclass=pymysql.cursors.DictCursor,autocommit=False)
def main():
 load_dotenv(ROOT/'.env',override=False);OUT.mkdir(parents=True,exist_ok=True)
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):db=ReadOnlyDB(database='kingdomai')
 rows=[];coverage=[]
 try:
  with db.conn.cursor() as cur:
   cur.execute('SET SESSION MAX_EXECUTION_TIME=10000');cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
   for code in CODES:
    cur.execute('SELECT trade_date,stk_code,open,high,low,close,adjopen,adjhigh,adjlow,adjclose,volume,amount,turn_ratio FROM kcrp_stock_price WHERE stk_code=%s AND trade_date>=%s AND trade_date<%s ORDER BY trade_date LIMIT 2100',(code,'2019-01-01','2026-09-28'))
    part=cur.fetchall()
    for r in part:
     for k,v in r.items():r[k]=str(v) if k in ('trade_date','stk_code') else (None if v is None else float(v))
    rows.extend(part);coverage.append({'code':code,'rows':len(part),'first':part[0]['trade_date'] if part else None,'last':part[-1]['trade_date'] if part else None})
    print(code,len(part),flush=True)
 finally:db.conn.rollback();db.close_db()
 raw=''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in rows).encode()
 with gzip.GzipFile(filename=str(OUT/'daily_inputs.jsonl.gz'),mode='wb',mtime=0) as f:f.write(raw)
 (OUT/'input_manifest.json').write_text(json.dumps({'fetched_at_utc':datetime.now(timezone.utc).isoformat(),'source':'kingdomai.kcrp_stock_price','selection':'24 preselected A-share codes; 2019-01-01 <= date < 2026-09-28; limit 2100 each; read-only consistent snapshot','price_basis':'adjopen/adjhigh/adjlow/adjclose; existing provider labels these hfq; raw volume unchanged','historical_vintage':'current source snapshot, not historical point-in-time revisions','raw_jsonl_sha256':hashlib.sha256(raw).hexdigest(),'rows':len(rows),'coverage':coverage},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
