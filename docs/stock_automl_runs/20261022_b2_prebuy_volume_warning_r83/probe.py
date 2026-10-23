from pathlib import Path
import glob
import numpy as np
import pandas as pd

R = Path("/data/cyris/work")
p = pd.read_pickle(R/"b2_news_flow_combo_r75_20261022/panel306.pkl")
b = pd.read_csv(R/"b2_board_volume_r71_20261022/B2_V2放行连板量能特征_306笔.csv")
q = pd.read_csv(R/"b2_first_board_flow_r72_20261022/首板前量价资金趋势_709候选.csv")
ledger = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(str(R/"b2_news_flow_years_r76_20261022"/"20*_morning7_baseline_ledger.csv")))],ignore_index=True)
ledger[["股票","D0选中日"]]=ledger.id.str.split("|",expand=True)
p["股票"] = p["stock"]
p["D0选中日"] = p.d0_date.astype(str)
m = ledger[["股票","D0选中日","return"]].merge(p, on=["股票","D0选中日"], validate="one_to_one")
m = m.merge(b[["股票","D0选中日","选股日成交量_万股","首板前20日均量_万股","首板前20日最大量_万股","首板量除前20日均量","D0量除首板前20日均量","D0量除前一板量","首板一字","D0一字","已连板天数","D2D3连续收盘跌停"]],on=["股票","D0选中日"],validate="one_to_one")
m = m.merge(q[["股票","D0选中日","pre20_price_return","pre20_volume_trend_log","pre20_first_vol_over_max","first_price_impact_per_relvolume","first_signed_flow_proxy"]],on=["股票","D0选中日"],validate="one_to_one")
assert len(m)==306
m["all_locked"] = m["all_locked"].eq("是").astype(int)
m["close_floor"] = m["close_floor"].eq("是").astype(int)
m["D0年"]=m["D0选中日"].str[:4]
m["D1前四K量除板前20日均量"] = m.early_volume_ratio*m["D0量除首板前20日均量"]
m["D1前四K量除板前20日最大量"] = m.early_volume_ratio*m["选股日成交量_万股"]/m["首板前20日最大量_万股"]
m["D0量除板前20日最大量"] = m["选股日成交量_万股"]/m["首板前20日最大量_万股"]
m["D1早盘承接"] = m.body5 - m.pullback5
m["D1早盘放量回落"] = m["D1前四K量除板前20日均量"] * m.pullback5
print("n",len(m),"alllocked",m.all_locked.sum(),"d2d3",m.D2D3连续收盘跌停.sum(),"closefloor",m.close_floor.sum(),"positive",sum(m["return"]>0))
features=[
("D0","D0量除首板前20日均量"),("D0","D0量除板前20日最大量"),
("D0","首板量除前20日均量"),("D0","D0量除前一板量"),
("D0","已连板天数"),("D0","d0_turnover_pct"),("D0","close_sealed_minutes"),
("D0","last30_amount_fraction"),("D0","total_mv_yi"),("D0","d0_return"),
("D0","d0_open_gap"),("D0","d0_range"),("D0","d0_close_position"),
("D0","pre20_price_return"),("D0","pre20_volume_trend_log"),("D0","pre20_first_vol_over_max"),
("D0","first_price_impact_per_relvolume"),("D0","first_signed_flow_proxy"),
("D1 09:34","early_volume_ratio"),("D1 09:34","D1前四K量除板前20日均量"),("D1 09:34","D1前四K量除板前20日最大量"),
("D1 09:34","open_gap"),("D1 09:34","body5"),("D1 09:34","high5"),
("D1 09:34","low5"),("D1 09:34","pullback5"),("D1 09:34","position5"),
("D1 09:34","early_volume_accel"),("D1 09:34","early_down_volume"),("D1 09:34","early_up_count"),
("D1 09:34","early_slope"),("D1 09:34","early_path"),("D1 09:34","early_below_d0"),
("D1 09:34","early_drawdown"),("D1 09:34","early_upper"),("D1 09:34","early_lower"),
("D1 09:34","D1早盘承接"),("D1 09:34","D1早盘放量回落"),
]
rows=[]
for timing,f in features:
 x=pd.to_numeric(m[f],errors="coerce")
 train=x[m["D0年"].isin(["2022","2023","2024"])].dropna()
 if len(train)<120 or train.nunique()<5:continue
 for direction,threshold in [("低",train.quantile(.2)),("高",train.quantile(.8))]:
  flag=(x<=threshold if direction=="低" else x>=threshold) & x.notna()
  for yr in ["train","2025","2026"]:
   mask=m["D0年"].isin(["2022","2023","2024"]) if yr=="train" else m["D0年"].eq(yr)
   z=m[mask&flag]; whole=m[mask]
   if len(z)==0:continue
   wins=z[z["return"]>0];loss=z[z["return"]<0]
   rows.append({
    "time":timing,"feature":f,"direction":direction,"train_cutoff":threshold,"year":yr,
    "n":len(z),"total":len(whole),"rate":len(z)/len(whole),
    "locked":int(z.all_locked.sum()),"locked_base":int(whole.all_locked.sum()),
    "d2d3":int(z.D2D3连续收盘跌停.sum()),"d2d3_base":int(whole.D2D3连续收盘跌停.sum()),
    "closefloor":int(z.close_floor.sum()),"closefloor_base":int(whole.close_floor.sum()),
    "winners_missed":len(wins),"winners_gt10":int((z["return"]>.10).sum()),
    "earnings_missed":wins["return"].sum()*10000,
    "loss_avoided":-loss["return"].sum()*10000,
    "net_if_skip":-z["return"].sum()*10000,
   })
out=pd.DataFrame(rows)
out.to_csv(R/"b2_prebuy_volume_warning_r83_20261022"/"单因子训练20%阈值_逐期经济风险.csv",index=False,encoding="utf-8-sig")
w=out.pivot(index=["time","feature","direction"],columns="year",values=["n","locked","d2d3","winners_missed","winners_gt10","net_if_skip"])
w.columns=[a+"_"+b for a,b in w.columns]
w=w.reset_index()
w=w[(w.n_2025.between(5,25))&(w.n_2026.between(8,40))]
w=w.sort_values(["net_if_skip_2025","net_if_skip_2026"],ascending=False)
print("Best 2025+2026 economically; all exploratory")
print(w[["time","feature","direction","n_2025","locked_2025","d2d3_2025","winners_missed_2025","net_if_skip_2025","n_2026","locked_2026","d2d3_2026","winners_missed_2026","net_if_skip_2026"]].head(25).to_string(index=False))
print("Volume related")
print(w[w.feature.str.contains("量|volume|turn|成交|一板|早盘")][["time","feature","direction","n_2025","locked_2025","d2d3_2025","winners_missed_2025","net_if_skip_2025","n_2026","locked_2026","d2d3_2026","winners_missed_2026","net_if_skip_2026"]].head(30).to_string(index=False))
