"""Compare train/validation/test ranking and export exact-minute daily top five."""
from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import pandas as pd

from scripts.experiment_automl_close9_models import (
    DATA, MARKET, OUT, day_weights, models, split_dates,
)


DETAIL_COLUMNS = (
    "模型", "样本段", "信号日", "次交易日", "名次", "前二", "前三", "前五",
    "股票代码", "股票名称", "模型分数", "14:40买价", "九分钟最高收盘价",
    "09:40收盘价", "最高可卖涨幅", "09:40卖出涨幅", "真实类别", "严重错误",
    "14:40涨幅", "量比", "14:40换手率%", "流通市值亿元",
    "五日量四日递增", "均线多头", "日内始终高于均线",
)
NAME = {"seven_logistic": "七因子逻辑回归", "five_logistic": "五因子逻辑回归",
        "shallow_tree": "浅决策树（并列分数）"}
PART = {"train": "训练集", "validation": "选模集", "test": "测试集"}


def report(output: Path):
    rows = pd.read_csv(DATA, dtype={"symbol6": str})
    states = pd.read_csv(MARKET)
    rows["partition"] = rows.signal_date.map(split_dates(states))
    if rows.partition.isna().any():
        raise ValueError("Incomplete date assignment")
    train = rows[rows.partition.eq("train")]
    weights = day_weights(train)
    saved = joblib.load(OUT / "selected_close9_model.joblib")
    summary, detail = [], []
    for key, (columns, model) in models().items():
        if "logistic" in key:
            model.fit(train[list(columns)], train.target_class.eq(1).astype(int),
                      logisticregression__sample_weight=weights)
        else:
            model.fit(train[list(columns)], train.target_class.eq(1).astype(int),
                      sample_weight=weights)
        if key == "five_logistic":
            check = (model.predict_proba(rows[list(columns)])[:, 1] -
                     saved.predict_proba(rows[list(columns)])[:, 1])
            if abs(check).max() > 1e-12:
                raise ValueError("Refitted five-factor model differs from saved model")
        for partition in ("train", "validation", "test"):
            population = rows[rows.partition.eq(partition)].copy()
            scored = population.copy()
            scored["score"] = model.predict_proba(scored[list(columns)])[:, 1]
            scored = scored.sort_values(["signal_date", "score", "symbol6"],
                                        ascending=[True, False, True])
            scored["rank"] = scored.groupby("signal_date").cumcount() + 1
            for n in (2, 5):
                chosen = scored[scored["rank"].le(n)]
                second_ties = []
                if n == 2:
                    for _, group in scored.groupby("signal_date"):
                        cutoff = group.score.iloc[1]
                        second_ties.append(int(group.score.ge(cutoff).sum()))
                summary.append({
                    "模型": NAME[key], "样本段": PART[partition], "交易日数": population.signal_date.nunique(),
                    "候选股票数": len(population), "每日取前": n, "选中股票数": len(chosen),
                    "目标超过1%": int(chosen.target_class.eq(1).sum()),
                    "中间0.5%-1%": int(chosen.target_class.eq(0).sum()),
                    "严重错误低于0.5%": int(chosen.target_class.eq(-1).sum()),
                    "目标占比": chosen.target_class.eq(1).mean(),
                    "严重错误占比": chosen.target_class.eq(-1).mean(),
                    "候选池目标占比": population.target_class.eq(1).mean(),
                    "目标Lift": chosen.target_class.eq(1).mean() /
                    population.target_class.eq(1).mean(),
                    "第二名并列最多股票数": max(second_ties) if second_ties else None,
                })
            for row in scored[scored["rank"].le(5)].itertuples():
                detail.append({
                    "模型": NAME[key], "样本段": PART[partition], "信号日": row.signal_date,
                    "次交易日": row.next_date, "名次": int(row.rank),
                    "前二": int(row.rank <= 2), "前三": int(row.rank <= 3), "前五": 1,
                    "股票代码": row.symbol6, "股票名称": row.name, "模型分数": row.score,
                    "14:40买价": row.entry_1440,
                    "九分钟最高收盘价": max(getattr(row, f"close_{m:02}") for m in range(32, 41)),
                    "09:40收盘价": row.close_40, "最高可卖涨幅": row.max_close9_return,
                    "09:40卖出涨幅": row.close_0940_return,
                    "真实类别": int(row.target_class),
                    "严重错误": int(row.target_class == -1),
                    "14:40涨幅": row.signal_return, "量比": row.volume_ratio,
                    "14:40换手率%": row.turnover_so_far_pct,
                    "流通市值亿元": row.float_mv_100m_cny,
                    "五日量四日递增": row.volume_4of5_increasing,
                    "均线多头": row.ma_bull_5_10_20,
                    "日内始终高于均线": row.all_intraday_lows_above_ma,
                })
    output.mkdir(parents=True, exist_ok=True)
    summary_frame = pd.DataFrame(summary)
    detail_frame = pd.DataFrame(detail, columns=DETAIL_COLUMNS)
    if len(detail_frame) != 600 or summary_frame.shape[0] != 18:
        raise ValueError("Expected three models × forty dates × five and 18 summary rows")
    summary_frame.to_csv(output / "模型三段效果.csv", index=False)
    detail_frame.to_csv(output / "每日前五明细.csv", index=False)
    print(summary_frame.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/stock_automl/close9_rank_review"))
    args = parser.parse_args()
    report(args.output_dir)
