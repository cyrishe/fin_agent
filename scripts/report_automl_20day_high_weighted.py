"""Report the 20-day highest-price, boundary-weighted rolling picks."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import FEATURES
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_matrix_10bar import PRICE_COLUMNS


SOURCE = Path("docs/stock_automl_runs/20261009_fuzzy_boundary/daily_top2.csv")
OUT = Path("docs/stock_automl_runs/20261009_high20_weighted_result")


def main():
    rows = pd.read_csv(SOURCE, dtype={"symbol6": str})
    weighted = rows[(rows.window.eq(20)) & (rows.train_price.eq("最高价")) &
                    (rows.method.eq("边界降权"))].copy()
    hard = rows[(rows.window.eq(20)) & (rows.train_price.eq("最高价")) &
                (rows.method.eq("硬标签"))].copy()
    if (len(weighted), weighted.signal_date.nunique(), len(hard)) != (40, 20, 40):
        raise ValueError("Expected two picks on 20 test dates")
    if not (weighted.train_end.lt(weighted.signal_date)).all():
        raise ValueError("Training reached a test date")
    candidates = pd.read_csv(DATA, dtype={"symbol6": str})
    detail = weighted.merge(candidates[["signal_date", "symbol6", *FEATURES]],
                            on=["signal_date", "symbol6"], validate="one_to_one")
    prior_keys = hard[["signal_date", "symbol6"]].assign(in_hard=1)
    detail = detail.merge(prior_keys, on=["signal_date", "symbol6"], how="left",
                          validate="one_to_one")
    detail["in_hard"] = detail.in_hard.fillna(0).astype(int)
    detail = detail.sort_values(["signal_date", "rank"])
    if not detail.signal_return.between(.03, .06).all():
        raise ValueError("Unexpected candidate filter")
    stock = detail.rename(columns={
        "signal_date": "信号日", "next_date": "次交易日", "train_start": "训练开始",
        "train_end": "训练结束", "rank": "名次", "symbol6": "股票代码", "name": "股票名称",
        "entry_1440": "14:40假设买价", "score": "排序分_P达标减P严重",
        "p_positive": "预测达标概率", "p_neutral": "预测中性概率",
        "p_negative": "预测严重概率", "in_hard": "硬标签也入选",
        "return_max_open": "开盘价验证涨幅", "class_max_open": "开盘价类别",
        "return_max_close": "收盘价验证涨幅", "class_max_close": "收盘价类别",
        "return_max_high": "最高价验证涨幅", "class_max_high": "最高价类别",
        "signal_return": "14:40涨幅", "volume_ratio": "量比",
        "turnover_so_far_pct": "14:40换手率百分数",
        "float_mv_100m_cny": "T-1流通市值亿元",
        "volume_4of5_increasing": "五日成交量四日递增",
        "ma_bull_5_10_20": "均线多头",
        "all_intraday_lows_above_ma": "日内低价高于均线",
    })
    stock = stock[["信号日", "次交易日", "训练开始", "训练结束", "名次", "股票代码",
                   "股票名称", "硬标签也入选", "14:40假设买价", "排序分_P达标减P严重",
                   "预测达标概率", "预测中性概率", "预测严重概率",
                   "开盘价验证涨幅", "开盘价类别", "收盘价验证涨幅", "收盘价类别",
                   "最高价验证涨幅", "最高价类别", "14:40涨幅", "量比",
                   "14:40换手率百分数", "T-1流通市值亿元", "五日成交量四日递增",
                   "均线多头", "日内低价高于均线"]]
    daily = []
    for day, group in detail.groupby("signal_date", sort=True):
        item = {"信号日": day, "次交易日": group.next_date.iloc[0], "入选总数": len(group)}
        for label, col in (("开盘", "class_max_open"), ("收盘", "class_max_close"),
                           ("最高", "class_max_high")):
            item[f"{label}达标"] = int(group[col].eq(1).sum())
            item[f"{label}中性"] = int(group[col].eq(0).sum())
            item[f"{label}严重错误"] = int(group[col].eq(-1).sum())
        daily.append(item)
    daily_frame = pd.DataFrame(daily)
    pool = candidates[candidates.signal_date.isin(detail.signal_date.unique())].merge(
        pd.read_csv("docs/stock_automl_runs/20261009_tenbar_matrix/tenbar_price_extrema.csv.gz",
                    dtype={"symbol6": str}),
        on=["next_date", "symbol6"], validate="one_to_one")
    summary = []
    for price, col in PRICE_COLUMNS.items():
        actual = detail[f"class_{col}"]
        old = hard[f"class_{col}"]
        base = pool[col]/pool.entry_1440-1
        hit, neutral, critical = [int(actual.eq(i).sum()) for i in (1, 0, -1)]
        summary.append({"验证价格": price, "测试日数": 20, "选出总数": 40,
                        "达标_大于1%": hit, "中性_0%至1%": neutral,
                        "严重错误_低于0%": critical, "达标率": hit/40,
                        "非负率": (hit+neutral)/40,
                        "候选池达标率": float(base.gt(.01).mean()),
                        "候选池非负率": float(base.ge(0).mean()),
                        "达标Lift": hit/40/base.gt(.01).mean(),
                        "非负Lift": (hit+neutral)/40/base.ge(0).mean(),
                        "硬标签达标": int(old.eq(1).sum()),
                        "硬标签严重错误": int(old.eq(-1).sum())})
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary).to_csv(OUT/"summary.csv", index=False)
    daily_frame.to_csv(OUT/"daily.csv", index=False)
    stock.to_csv(OUT/"stocks.csv", index=False)
    (OUT/"manifest.json").write_text(json.dumps({
        "definition": "20 previous available signal days, highest-price training label, boundary downweight, top two daily",
        "dates": sorted(weighted.signal_date.unique().tolist()),
        "same_as_hard_count": int(detail.in_hard.sum()),
        "weighted_only_count": int((1-detail.in_hard).sum()),
        "source": str(SOURCE),
    }, ensure_ascii=False, indent=2)+"\n")
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    main()
