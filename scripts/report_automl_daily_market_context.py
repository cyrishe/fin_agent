"""Join saved rolling-model predictions to outcomes and prior-day market context.

This is a read-only report. Market figures come from kingdomai.kcrp_stock_price;
model predictions and realized minute prices come from the saved experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query


ROOT = Path("docs/stock_automl_runs/20261011_mk_1m_tradable_st_rolling20")
CHECKPOINTS = Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_rolling20_checkpoints")
CANDIDATES = Path("outputs/stock_automl/mk_1m_jan_jul/tradable_st_candidates.csv.gz")
MARKET_SQL = """SELECT trade_date,
    SUM(close > preclose) AS up_count,
    SUM(close < preclose) AS down_count,
    SUM(amount) / 100000000 AS amount_100m_cny,
    COUNT(*) AS listed_count,
    SUM(amount > 0) AS traded_count
FROM kcrp_stock_price
WHERE trade_date BETWEEN %s AND %s
  AND RIGHT(stk_code, 3) IN ('.SH', '.SZ')
  AND LEFT(stk_code, 1) IN ('0', '3', '6')
  AND preclose > 0 AND close > 0
GROUP BY trade_date ORDER BY trade_date"""


def build_daily(scores: pd.DataFrame, candidates: pd.DataFrame,
                top1: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    scores = scores.copy()
    candidates = candidates.copy()
    top1 = top1.copy()
    for frame in (scores, candidates, top1):
        frame["signal_date"] = frame.signal_date.astype(str)
        frame["symbol6"] = frame.symbol6.astype(str).str.zfill(6)
    if scores.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Duplicate scored stock-day")
    joined = scores.merge(
        candidates[["signal_date", "symbol6", "signal_price", "second_high", "label_complete"]],
        on=["signal_date", "symbol6"], how="left", validate="one_to_one", indicator=True,
        suffixes=("_score", "_candidate"))
    if joined._merge.ne("both").any():
        raise ValueError("Scored candidate missing from source cohort")
    if not joined.signal_price_score.eq(joined.signal_price_candidate).all():
        raise ValueError("Saved scores and candidate entry prices disagree")
    actual_return = joined.second_high / joined.signal_price_score - 1
    predicted_up = joined.predicted_class.isin(("ge3", "1to3"))
    rows = []
    for day, group in joined.groupby("signal_date", sort=True):
        selected = group.loc[predicted_up.loc[group.index]]
        labeled = selected.loc[selected.label_complete & selected.second_high.notna()]
        outcome = actual_return.loc[labeled.index]
        rows.append({"T_date": day, "candidate_n": len(group), "predicted_up_n": len(selected),
                     "predicted_up_labeled_n": len(labeled),
                     "predicted_up_unlabeled_n": len(selected) - len(labeled),
                     "target_ge3_n": int(outcome.ge(.03).sum()),
                     "acceptable_1to3_n": int((outcome.ge(.01) & outcome.lt(.03)).sum()),
                     "error_lt0_n": int(outcome.lt(0).sum())})
    daily = pd.DataFrame(rows)
    if top1.duplicated("signal_date").any():
        raise ValueError("Duplicate Top1 day")
    daily = daily.merge(top1[["signal_date", "symbol6", "name", "actual_class",
                              "actual_0940_return"]].rename(columns={
                                  "signal_date": "T_date", "symbol6": "top1_symbol6",
                                  "name": "top1_name", "actual_class": "top1_target_class",
                                  "actual_0940_return": "top1_0940_return"}),
                        on="T_date", how="left", validate="one_to_one")
    market = market.copy()
    market["trade_date"] = pd.to_datetime(market.trade_date).dt.strftime("%Y-%m-%d")
    if market.trade_date.duplicated().any():
        raise ValueError("Duplicate market date")
    dates = market.trade_date.tolist()
    positions = {date: index for index, date in enumerate(dates)}
    for i, row in daily.iterrows():
        day = row.T_date
        if day not in positions or positions[day] < 5:
            raise ValueError(f"Five prior market days unavailable for {day}")
        previous = market.iloc[positions[day] - 1]
        trailing = market.iloc[positions[day] - 5:positions[day]]
        daily.loc[i, "T_minus_1_date"] = previous.trade_date
        daily.loc[i, "T_minus_1_up"] = int(previous.up_count)
        daily.loc[i, "T_minus_1_down"] = int(previous.down_count)
        daily.loc[i, "T_minus_1_listed"] = int(previous.listed_count)
        daily.loc[i, "T_minus_1_traded"] = int(previous.traded_count)
        daily.loc[i, "T_minus_5_to_minus_1_amount_100m_cny"] = "; ".join(
            f"{item.trade_date}:{float(item.amount_100m_cny):.2f}" for item in trailing.itertuples())
    daily["top1_0940_return_pct"] = daily.top1_0940_return * 100
    daily = daily.drop(columns="top1_0940_return")
    if daily.predicted_up_labeled_n.lt(daily.target_ge3_n + daily.acceptable_1to3_n + daily.error_lt0_n).any():
        raise ValueError("Outcome counts exceed predicted-up candidates")
    return daily


def render_markdown(daily: pd.DataFrame, source_commit: str) -> str:
    lines = ["# 20 日保留可入场 ST 模型：逐日效果与前一日大盘", "",
             f"模型结果来源：`{source_commit}`。T 为 14:40 信号日；Top1 收益在 T+1 09:40 分钟收盘价实现。", "",
             "**模型本身效果**统计当日全部被四分类模型判为 `≥3%` 或 `1%–3%`、且次晨标签完整的候选；"
             "这不是每日实际买入数。达标为次晨 09:31–09:40 第二高价相对 T 14:40 价 `≥3%`，"
             "可接受为 `1%–3%`，严重错误为 `<0%`。`0%–1%` 中性未展示；"
             "有标签正向信号数减去这三列即中性数；缺标签的正向信号另列，不参与评价。"
             "Top1 按原规则从正向候选中取一只，收益单独按次晨 09:40 收盘价计算，"
             "不代表在第二高价卖出。", "",
             "大盘 T−1 涨跌家数来自 `kingdomai.kcrp_stock_price` 收盘价对昨收价；平盘未计入。"
             "范围是沪深 A 股代码（`.SH/.SZ`，首位 `0/3/6`），停牌且价格有效者可计入挂牌数；"
             "近五个交易日成交额为同表逐股 `amount` 求和，单位亿元，按日期从早到晚，截止 T−1，"
             "不含 T 当天数据。成交额为数据库覆盖口径，未与交易所公报逐日对账。", ""]
    columns = ["T", "有标签正向信号", "缺标签", "达标", "可接受", "严重错误", "Top1", "Top1目标",
               "09:40收益", "T−1涨/跌", "T−5…T−1成交额（亿元）"]
    for month, group in daily.groupby(daily.T_date.str[:7], sort=True):
        lines += [f"## {month}", "", "| " + " | ".join(columns) + " |",
                  "|" + "|".join(["---"] * len(columns)) + "|"]
        for row in group.itertuples():
            target = {"ge3": "达标", "1to3": "可接受", "lt0": "严重错误"}.get(
                row.top1_target_class, "—")
            top1 = f"{row.top1_symbol6} {row.top1_name}" if isinstance(row.top1_symbol6, str) else "无交易"
            profit = f"{row.top1_0940_return_pct:+.2f}%" if pd.notna(row.top1_0940_return_pct) else "—"
            amounts = row.T_minus_5_to_minus_1_amount_100m_cny.replace("; ", "<br>")
            lines.append("| " + " | ".join(map(str, [row.T_date, row.predicted_up_labeled_n,
                row.predicted_up_unlabeled_n,
                row.target_ge3_n, row.acceptable_1to3_n, row.error_lt0_n,
                top1, target, profit,
                f"{row.T_minus_1_date} {int(row.T_minus_1_up)}/{int(row.T_minus_1_down)}", amounts])) + " |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    files = sorted(CHECKPOINTS.glob("*_scores.csv.gz"))
    if not files:
        raise ValueError("No saved daily score files")
    scores = pd.concat((pd.read_csv(path, dtype={"symbol6": str}) for path in files), ignore_index=True)
    candidates = pd.read_csv(CANDIDATES, dtype={"symbol6": str})
    top1_path = ROOT / "top1_verified.csv"
    top1 = pd.read_csv(top1_path, dtype={"symbol6": str})
    with db_connection(args.env_file) as conn:
        market = query(conn, MARKET_SQL, ("2026-01-01", "2026-07-30"))
    daily = build_daily(scores, candidates, top1, market)
    ROOT.mkdir(parents=True, exist_ok=True)
    daily.to_csv(ROOT / "daily_effect_market_context.csv", index=False, float_format="%.6f")
    market.to_csv(ROOT / "market_daily_context.csv", index=False, float_format="%.6f")
    source_commit = __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    (ROOT / "daily_effect_market_context.md").write_text(
        render_markdown(daily, source_commit), encoding="utf-8")
    manifest = {"source_commit": source_commit, "days": len(daily),
                "scored_candidates": len(scores), "predicted_up": int(daily.predicted_up_n.sum()),
                "top1_trades": int(daily.top1_symbol6.notna().sum()),
                "market_query": MARKET_SQL,
                "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in [CANDIDATES, top1_path, *files]},
                "market_daily_sha256": hashlib.sha256(
                    (ROOT / "market_daily_context.csv").read_bytes()).hexdigest()}
    (ROOT / "daily_effect_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: manifest[key] for key in
                      ("days", "scored_candidates", "predicted_up", "top1_trades")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
