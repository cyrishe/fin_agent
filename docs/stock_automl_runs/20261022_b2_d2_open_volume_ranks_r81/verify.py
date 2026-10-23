from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd
root=Path('/data/cyris/work/b2_d2_open_volume_ranks_r81_20261022')
r80=Path('/data/cyris/work/b2_d2_open_volume_r80_20261022')
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):
            h.update(b)
    return h.hexdigest()
x=pd.read_csv(root/'100笔_开盘量能候选变量.csv',dtype={'股票':str})
s=pd.read_csv(root/'变量高低极值_good_bad.csv')
t=pd.read_csv(root/'变量三等分_good_bad.csv')
r=pd.read_csv(root/'逐变量逐笔高低排序.csv',dtype={'股票':str})
a=pd.read_csv(root/'09_31仍封死19笔_核心变量与结局.csv',dtype={'股票':str})
c=pd.read_csv(root/'开盘未跌停51笔_09_31变量与触板后结局.csv',dtype={'股票':str})
assert len(x)==100 and len(a)==19 and a.股票.nunique()==18 and len(c)==51
assert int(a['D2全天一价封死'].sum())==13
assert int(a['首触后曾有后续完整分钟离板'].sum())==6
assert int(c['首触后曾有后续完整分钟离板'].sum())==47
assert len(s)==32 and len(r)==1003
f05=s[(s['时点与样本']=='A_开盘跌停且09_31仍封死')&(s['变量']=='F05_首分钟对板前20日同刻最大')].iloc[0]
assert [int(f05[z]) for z in ['低端good','低端bad','中段good','中段bad','高端good','高端bad']]==[0,5,2,7,4,1]
f08=s[(s['时点与样本']=='A_开盘跌停且09_31仍封死')&(s['变量']=='F08_首分钟占D1全天量')].iloc[0]
assert int(f08['高端good'])==0 and int(f08['高端bad'])==5
raw=json.loads((root/'raw_audit.json').read_text())
assert raw['source_level_d2_files']==19 and raw['source_level_preboard_stock_days']==360
assert raw['F05_low_middle_high_good_bad']==[[0,5],[2,7],[4,1]]
assert len(pd.read_csv(root/'原始分钟独立复算19笔.csv'))==19
third=t[(t['时点与样本']=='C_开盘未跌停且09_31前未触板')&(t['变量']=='F05_首分钟对板前20日同刻最大')].sort_values('低到高三等分')
assert third.good.tolist()==[17,14,16] and third.bad.tolist()==[0,3,1]
pre=pd.read_csv(r80/'首板前同刻分钟源_逐股日.csv.gz',dtype={'stk_code':str})
p=pd.read_csv(r80/'preboard_daily_selected.csv.gz',dtype={'stk_code':str})
v=pre[pre['有效'].eq(1)].set_index(['trade_date','stk_code'])
for _,row in a.iterrows():
    code=row['股票'];board=x.loc[(x['股票']==code)&(x['D0选中日']==row['D0选中日']),'首板日'].iloc[0]
    days=p[(p.stk_code==code)&(p['对应首板日']==board)].sort_values('trade_date',ascending=False).head(20).trade_date.tolist()
    assert len(days)==20
    h=v.loc[[(d,code) for d in days],'开盘首分钟量_股']
    calculated=float(row['开盘首分钟量_股'])/float(h.max())
    assert np.isclose(calculated,float(row['首分钟量除板前20日同刻最大']),rtol=1e-6)
for file in ['变量高低极值_good_bad.csv','变量三等分_good_bad.csv','极值取3_5_7笔敏感性.csv',
             '09_31仍封死19笔_核心变量与结局.csv']:
    frame=pd.read_csv(root/file)
    assert not any(frame[col].astype(str).str.startswith("'").any() for col in frame.select_dtypes(include='number').columns)
verification={'sample':100,'main_0931_still_sealed':19,'main_unique_stocks':18,
              'main_good':6,'main_bad':13,'nonopening_before_0931_touch':51,
              'nonopening_good':47,'nonopening_bad':4,'rank_rows':len(r),
              'summary_rows':len(s),'f05_low5_good_bad':[0,5],
              'f05_middle9_good_bad':[2,7],'f05_high5_good_bad':[4,1],
              'f08_high5_good_bad':[0,5],
              'all_main_f05_recomputed_from_R80_preboard_minutes':True}
(root/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
source={}
for key,path in {
 'R80_100_trades':r80/'100笔D2开盘同刻量比.csv',
 'R80_preboard_minutes':r80/'首板前同刻分钟源_逐股日.csv.gz',
 'R80_preboard_daily':r80/'preboard_daily_selected.csv.gz',
 'R80_manifest':r80/'manifest.json',
}.items():
    source[key]={'server_path':str(path),'sha256':sha(path),'bytes':path.stat().st_size}
outputs={}
for path in sorted(root.iterdir()):
    if path.is_file() and path.name!='manifest.json':
        outputs[path.name]={'sha256':sha(path),'bytes':path.stat().st_size}
(root/'manifest.json').write_text(json.dumps({'source_inputs':source,'outputs':outputs},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(verification,ensure_ascii=False))
