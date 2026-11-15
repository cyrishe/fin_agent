from pathlib import Path
import sys,subprocess,shutil,pandas as pd
R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;B=R/'outputs/b2连续两日全量回放_20261113';O=R/'outputs/多年份训练与组合对照_20261113';year=sys.argv[1];q=P/'账户'/year;py=sys.executable
def run(*args):
 print('运行',*map(str,args),flush=True);subprocess.run([str(a) for a in args],cwd=R,check=True)
for f in ('核验.json','份额核验.json'):(q/f).unlink(missing_ok=True)
run(py,P/'准备组合账户.py',year,*sys.argv[2:3])
if not len(pd.read_pickle(q/'candidates.pkl')):raise SystemExit('没有完整月份')
for f in ['daily.py','scan.py']:shutil.copy2(B/f,q/f)
run(py,q/'daily.py');run(py,q/'scan.py')
sup=[]
if year=='2026' and pd.read_csv(q/'行情请求.csv').day.ge('2026-10-01').any():
 shutil.copy2(B/'年度/2026/db_after_sep.py',q/'db_after_sep.py');run(py,q/'db_after_sep.py');sup.append(pd.read_pickle(q/'日线补充.pkl.gz'))
dirs=[B/'年度'/year,R/'outputs/D0分时形态研究_20261113/分时账户回放'/year,R/'outputs/D0分时形态研究_20261113/远期账户回放'/year,R/'outputs/连板选股收益优化_20261113/新模型全量回放'/year,O/'账户'/year]
if year=='2024':dirs.append(R/'outputs/训练频率六个月对照_20261113/账户')
ev=[]
if (q/'独立行情核验.csv').exists():ev.append(pd.read_csv(q/'独立行情核验.csv'))
for d in dirs:
 if (d/'日线补充.pkl.gz').exists():
  x=pd.read_pickle(d/'日线补充.pkl.gz')
  if len(x):sup.append(x)
 if (d/'独立行情核验.csv').exists():
  try:ev.append(pd.read_csv(d/'独立行情核验.csv'))
  except pd.errors.EmptyDataError:pass
if ev:
 e=pd.concat(ev,ignore_index=True);g=e.groupby(['symbol','day'])['独立行情状态'].nunique();good=set(g[g.eq(1)].index);mask=[((r.symbol,r.day) in good) for r in e.itertuples()];conflicts=e[[not v for v in mask]]
 if len(conflicts):conflicts.to_csv(q/'行情证据冲突.csv',index=False)
 e=e[mask];e.drop_duplicates(['symbol','day']).to_csv(q/'独立行情核验.csv',index=False)
(pd.concat(sup,ignore_index=True).drop_duplicates(['symbol','day']) if sup else pd.DataFrame(columns=['symbol','day'])).to_pickle(q/'日线补充.pkl.gz',compression='gzip')
(q/'account.py').write_text((O/'账户程序.py').read_text().replace("sorted((S/'分钟').glob('*.pkl.gz'))", "sorted(p for p in (S/'分钟').glob('*.pkl.gz') if not p.name.startswith('.'))"));(q/'verify.py').write_text((O/'核验账户.py').read_text().replace("sorted(P.glob('*_逐批次.csv'))", "sorted(p for p in P.glob('*_逐批次.csv') if not p.name.startswith('.'))"));run(py,q/'account.py',year,'原始10');run(py,q/'verify.py');run(py,O/'核验份额.py',q,year);run(py,P/'核验回撤.py',q,year)
