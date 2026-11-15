from pathlib import Path
P=Path(__file__).parent
original=P.parent/'最近两周三模型_20261113/逐笔与账户回放.py'
s=original.read_text()
old="R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;O=P/'十一月独立' if '--十一月' in sys.argv else P;O.mkdir(exist_ok=True);"
new="R=Path('/Volumes/ext/fin_agent');P=R/'outputs/最近两周三模型_20261113';O=R/'outputs/三模型投票分析_20261115/票数加权账户';O.mkdir(exist_ok=True);"
assert old in s;s=s.replace(old,new)
old="n=len(dayrows);allocation=cash/n if n else 0\n for r in dayrows.itertuples():\n  quote="
new="n=len(dayrows);budget=cash;weightsum=dayrows['票数'].sum()\n for r in dayrows.itertuples():\n  allocation=budget*r.票数/weightsum\n  quote="
assert old in s;s=s.replace(old,new)
(P/'票数加权回放.py').write_text(s)
