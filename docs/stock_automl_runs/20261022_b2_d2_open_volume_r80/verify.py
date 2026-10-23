from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd

root=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
r79=Path('/data/cyris/work/b2_d2_floor_volume_r79_20261022')
r71=Path('/data/cyris/work/b2_board_volume_r71_20261022')
r76=Path('/data/cyris/work/b2_news_flow_years_r76_20261022')
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):
            h.update(b)
    return h.hexdigest()

x=pd.read_csv(root/'100笔D2开盘同刻量比.csv',dtype={'股票':str})
p=pd.read_csv(root/'preboard_daily_selected.csv.gz',dtype={'stk_code':str})
pre=pd.read_csv(root/'首板前同刻分钟源_逐股日.csv.gz',dtype={'stk_code':str})
src=json.loads((root/'首板前压缩源清单.json').read_text())
assert len(x)==100 and x.股票.nunique()==95
assert int(x['D2开盘跌停'].sum())==34 and int(x['D2全天一价封死'].sum())==14
assert len(x[x['D2开盘跌停'].eq(1)].股票.unique())==32
assert int(x['首分钟整根封死'].mul(x['D2开盘跌停']).sum())==21
assert int(x['板前5日同刻完整'].sum())==91
assert int(x.loc[x['D2开盘跌停'].eq(1),'板前5日同刻完整'].sum())==32
assert len(p)==1880 and len(pre)==1880 and int(pre['有效'].sum())==1806
assert len(src)==562 and len({z['date'] for z in src})==562
valid=pre[pre['有效'].eq(1)].set_index(['trade_date','stk_code'])
for _,row in valid.iterrows():
    assert abs(row['全天分钟合计量_股']-row['官方日成交量_股'])<=max(5000,.0001*row['官方日成交量_股'])
checked=0
for _,r in x.iterrows():
    h=p[(p.stk_code==r['股票'])&(p['对应首板日']==r['首板日'])].sort_values('trade_date',ascending=False)
    for n in (5,10,20):
        if not r[f'板前{n}日同刻完整']:continue
        dates=h.head(n).trade_date.tolist()
        assert len(dates)==n and all((d,r['股票']) in valid.index for d in dates)
        for name in ('开盘首分钟量_股','开盘前5分钟量_股','开盘前10分钟量_股'):
            mean=float(valid.loc[[(d,r['股票']) for d in dates],name].mean())
            ratio=float(r[name])/mean
            stored=float(r[f'D2{name}除板前{n}日同刻均值'])
            assert np.isclose(ratio,stored,rtol=2e-6,atol=2e-6),(r['股票'],n,name)
            checked+=1
verification={'sample':len(x),'opening_floor':int(x['D2开盘跌停'].sum()),
              'all_day_one_price':int(x['D2全天一价封死'].sum()),
              'opening_still_floor_through_0931':int(x['首分钟整根封死'].mul(x['D2开盘跌停']).sum()),
              'valid_preboard_stock_days':int(pre['有效'].sum()),
              'archived_source_days':len(src),'checked_ratio_cells':checked,
              'complete_baselines_all':int(x['板前5日同刻完整'].sum()),
              'complete_baselines_opening':int(x.loc[x['D2开盘跌停'].eq(1),'板前5日同刻完整'].sum())}
(root/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
sources={}
for key,path in {
    'R79_100_floor_trades':r79/'100笔D2触跌停_成交量逐笔.csv',
    'R71_preboard_daily':r71/'preboard_daily_source.csv.gz',
    'R76_d2_minute_manifest':r76/'minute_source_manifest.json',
}.items():
    assert path.exists(),path
    sources[key]={'server_path':str(path),'sha256':sha(path),'bytes':path.stat().st_size}
outputs={}
for path in sorted(root.iterdir()):
    if path.is_file() and path.name not in ('manifest.json','checkpoint_preboard.csv.gz','checkpoint_sources.json'):
        outputs[path.name]={'sha256':sha(path),'bytes':path.stat().st_size}
(root/'manifest.json').write_text(json.dumps({'source_inputs':sources,'server_raw_archive_manifest':'首板前压缩源清单.json','outputs':outputs},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(verification,ensure_ascii=False))
