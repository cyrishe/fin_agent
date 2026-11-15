from pathlib import Path
P=Path(__file__).parent; src=P.parent/'最近两周三模型_20261113/逐笔与账户回放.py';s=src.read_text()
old="R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;O=P/'十一月独立' if '--十一月' in sys.argv else P;O.mkdir(exist_ok=True);"
new="R=Path('/Volumes/ext/fin_agent');P=R/'outputs/最近两周三模型_20261113';O=R/'outputs/涨停追击十一月复核_20261115/连续账户';O.mkdir(exist_ok=True);"
assert old in s;s=s.replace(old,new)
old="assert (len(sigs),sigs.d0_date.nunique())==((42,9) if '--十一月' in sys.argv else (47,10))"
new="assert '--十一月' in sys.argv and (len(sigs),sigs.d0_date.nunique())==(42,9);risk=pd.read_csv(O.parent/'风险明细.csv',dtype={'symbol':str});sigs=sigs.merge(risk[['d0_date','symbol','排除']],on=['d0_date','symbol'],validate='one_to_one');assert sigs.排除.notna().all();sigs=sigs[~sigs.排除].copy();assert (len(sigs),sigs.d0_date.nunique())==(37,9)"
assert old in s;s=s.replace(old,new);(P/'连续账户回放.py').write_text(s)
