from pathlib import Path
P=Path(__file__).parent
src=P.parent/'三模型全月_20261113/账户/2026/account.py'
s=src.read_text()
old="R=Path('/Volumes/ext/fin_agent');P=Path(__file__).parent;S=P;year=sys.argv[1];mode=sys.argv[2];assert mode in ('原始10','滑点05','原始5')"
new="R=Path('/Volumes/ext/fin_agent');year=sys.argv[1];mode=sys.argv[2];S=R/'outputs/三模型全月_20261113/账户'/year;P=R/'outputs/三模型投票分析_20261115/历史加权账户'/year;P.mkdir(parents=True,exist_ok=True);assert mode in ('原始10','滑点05','原始5')"
assert old in s;s=s.replace(old,new)
old="available=cash;slots=len(sigs[sigs.buy_day.eq(day)]);allocation=available/slots if slots else 0\n  for row in sigs[sigs.buy_day.eq(day)].sort_values('new_rank').to_dict('records'):\n   sym="
new="available=cash;dayrows=sigs[sigs.buy_day.eq(day)];weightsum=dayrows['选中次数'].sum()\n  for row in dayrows.sort_values('new_rank').to_dict('records'):\n   allocation=available*row['选中次数']/weightsum\n   sym="
assert old in s;s=s.replace(old,new)
(P/'历史加权回放.py').write_text(s)
