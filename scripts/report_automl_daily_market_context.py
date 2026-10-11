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
MODEL_SOURCE_COMMIT = "99c3b7f5edd61926a32e8b9fb0d98f3b3225a938"
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
            f"{float(item.amount_100m_cny):.2f}" for item in trailing.itertuples())
    daily["top1_0940_return_pct"] = daily.top1_0940_return * 100
    daily = daily.drop(columns="top1_0940_return")
    if daily.predicted_up_labeled_n.lt(daily.target_ge3_n + daily.acceptable_1to3_n + daily.error_lt0_n).any():
        raise ValueError("Outcome counts exceed predicted-up candidates")
    return daily


def render_markdown(daily: pd.DataFrame, source_commit: str) -> str:
    lines = ["# 逐日模型效果、第一名收益与前一交易日大盘", "",
             f"采用保留可交易特殊处理股票的二十日训练模型，模型结果来源：`{source_commit}`。"
             "每行日期是下午 14:40 的选股日；第一名收益在下一交易日 09:40 分钟收盘价实现。", "",
             "**模型效果**统计当日被模型预测为次晨上涨至少 1%、且次晨价格数据完整的全部股票。"
             "这不是每日实际买入数。达标指次晨 09:31–09:40 第二高价比选股日 14:40 价格上涨至少 3%；"
             "可接受指上涨 1% 至不足 3%；严重错误指跌破选股日 14:40 价格。"
             "上涨 0% 至不足 1% 的中性结果不展示；已验证股票数减去上述三类就是中性数。"
             "缺少次晨数据的股票单列，不参与评价。第一名按原选股规则确定，"
             "三列第一名结果中，1 表示属于该类，0 表示不属于；无交易时留空。"
             "收益单独按次晨 09:40 收盘价计算，不代表在第二高价卖出。", "",
             "前一交易日大盘涨跌家数来自 `kingdomai.kcrp_stock_price` 收盘价对昨收价；平盘未计入。"
             "范围是沪深 A 股代码（`.SH/.SZ`，首位 `0/3/6`），停牌且价格有效者可计入挂牌数；"
             "近五个交易日成交额为同表逐股 `amount` 求和，单位亿元，从较早交易日到较近交易日排列，"
             "截止选股日前一交易日，不含选股日数据。成交额为数据库覆盖口径，未与交易所公报逐日对账。", ""]
    columns = ["选股日", "预测上涨且已验证的股票数", "预测上涨但缺数据的股票数",
               "达标数", "可接受数", "严重错误数", "当天第一名股票", "第一名实际分类",
               "第一名达标", "第一名可接受", "第一名严重错误",
               "第一名次晨卖出收益", "前一交易日上涨/下跌家数", "此前五日沪深成交额（亿元，从远到近）"]
    for month, group in daily.groupby(daily.T_date.str[:7], sort=True):
        lines += [f"## {month[:4]} 年 {int(month[5:])} 月", "", "| " + " | ".join(columns) + " |",
                  "|" + "|".join(["---"] * len(columns)) + "|"]
        for row in group.itertuples():
            target = {"ge3": "达标", "1to3": "可接受", "lt0": "严重错误"}.get(
                row.top1_target_class, "—")
            flags = top1_flags(row.top1_symbol6, row.top1_target_class)
            top1 = f"{row.top1_symbol6} {row.top1_name}" if isinstance(row.top1_symbol6, str) else "无交易"
            profit = f"{row.top1_0940_return_pct:+.2f}%" if pd.notna(row.top1_0940_return_pct) else "—"
            amounts = row.T_minus_5_to_minus_1_amount_100m_cny.replace("; ", "<br>")
            lines.append("| " + " | ".join(map(str, [row.T_date, row.predicted_up_labeled_n,
                row.predicted_up_unlabeled_n,
                row.target_ge3_n, row.acceptable_1to3_n, row.error_lt0_n,
                top1, target, *("—" if value is None else value for value in flags), profit,
                f"{int(row.T_minus_1_up)}/{int(row.T_minus_1_down)}", amounts])) + " |")
        lines.append("")
    return "\n".join(lines)


def top1_flags(symbol: object, actual_class: object) -> tuple[int | None, int | None, int | None]:
    if not isinstance(symbol, str):
        return None, None, None
    return tuple(int(actual_class == label) for label in ("ge3", "1to3", "lt0"))


def export_daily(daily: pd.DataFrame) -> pd.DataFrame:
    columns = {
        "T_date": "选股日",
        "predicted_up_labeled_n": "预测上涨且已验证的股票数",
        "predicted_up_unlabeled_n": "预测上涨但缺数据的股票数",
        "target_ge3_n": "达标数",
        "acceptable_1to3_n": "可接受数",
        "error_lt0_n": "严重错误数",
        "top1_symbol6": "当天第一名股票代码",
        "top1_name": "当天第一名股票名称",
        "top1_target_class": "第一名实际分类",
        "top1_0940_return_pct": "第一名次晨卖出收益百分比",
        "T_minus_1_up": "前一交易日上涨家数",
        "T_minus_1_down": "前一交易日下跌家数",
        "T_minus_5_to_minus_1_amount_100m_cny": "此前五日沪深成交额亿元从远到近",
    }
    result = daily[list(columns)].rename(columns=columns)
    flags = [top1_flags(row.top1_symbol6, row.top1_target_class) for row in daily.itertuples()]
    for offset, (name, values) in enumerate(zip(
            ("第一名达标", "第一名可接受", "第一名严重错误"), zip(*flags))):
        result.insert(result.columns.get_loc("第一名实际分类") + 1 + offset,
                      name, pd.Series(values, dtype="Int64"))
    result["第一名实际分类"] = result["第一名实际分类"].map(
        {"ge3": "达标", "1to3": "可接受", "lt0": "严重错误"})
    for name in ("前一交易日上涨家数", "前一交易日下跌家数"):
        result[name] = result[name].astype(int)
    return result


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
    export_daily(daily).to_csv(ROOT / "daily_effect_market_context.csv", index=False, float_format="%.6f")
    market.rename(columns={"trade_date": "交易日", "up_count": "上涨家数",
                           "down_count": "下跌家数", "amount_100m_cny": "沪深成交额亿元",
                           "listed_count": "有效价格股票数", "traded_count": "有成交股票数"}).to_csv(
        ROOT / "market_daily_context.csv", index=False, float_format="%.6f")
    (ROOT / "daily_effect_market_context.md").write_text(
        render_markdown(daily, MODEL_SOURCE_COMMIT), encoding="utf-8")
    manifest = {"source_commit": MODEL_SOURCE_COMMIT, "days": len(daily),
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
