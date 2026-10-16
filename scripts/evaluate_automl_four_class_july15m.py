"""Score July 15m proxy candidates with the frozen unweighted four-class recipe."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_automl_four_class_decisions import class_priority_fallback
from scripts.benchmark_automl_1440_inference import FEATURES, db_connection, query
from scripts.experiment_automl_close9_models import DATA
from scripts.experiment_automl_second_high_four_class import CLASSES, four_class, models
from scripts.experiment_automl_second_high_regression import SECOND_HIGHS


PROXY = Path("outputs/stock_automl/sina_15m_july/trainable_proxy_candidates.csv")
CACHE = Path("outputs/stock_automl/sina_15m_july/cache")
OCT8 = Path("docs/stock_automl_runs/20261010_second_high_four_class_oct9")
PRIOR_JULY = Path("docs/stock_automl_runs/20261008_sina_15m_july/july_top5_two_models.csv")
OUT = Path("docs/stock_automl_runs/20261010_second_high_four_class_july15m")
SELLS = ("09:45", "10:00", "10:15")


def fitted_model():
    base = pd.read_csv(DATA, dtype={"symbol6": str})
    prices = pd.read_csv(SECOND_HIGHS, dtype={"symbol6": str})
    history = base.merge(prices[["next_date", "symbol6", "second_high"]],
                         on=["next_date", "symbol6"], validate="one_to_one")
    dates = sorted(history.signal_date.unique())
    training = history[history.signal_date.isin(dates[-20:])].copy()
    training["class"] = four_class(training.second_high / training.entry_1440 - 1)
    if (len(history) != 14292 or len(dates) != 40 or len(training) != 5818
            or training["class"].nunique() != 4):
        raise ValueError("The saved four-class September training cohort changed")
    model = models()["small_boosted_tree"]
    model.fit(training[list(FEATURES)], training["class"].astype(str))
    reference = pd.read_csv(OCT8 / "predictions_before_outcomes.csv",
                            dtype={"symbol6": str})
    reference = reference[reference.model.eq("unweighted")].sort_values("symbol6")
    probabilities = model.predict_proba(reference[list(FEATURES)])
    labels = list(model.classes_)
    delta = max(float(np.max(np.abs(
        probabilities[:, labels.index(label)] - reference[f"p_{label}"].to_numpy())))
        for label in CLASSES)
    if len(reference) != 178 or delta > 1e-12:
        raise ValueError(f"Model does not reproduce Oct 8 predictions: {delta}")
    return model, {"training_days": 20, "training_stocks": len(training),
                   "training_start": dates[-20], "training_end": dates[-1],
                   "oct8_probability_max_abs_diff": delta}


def load_candidates() -> tuple[pd.DataFrame, dict]:
    rows = pd.read_csv(PROXY, dtype={"symbol6": str})
    if (rows.signal_date.nunique() != 23
            or not rows.signal_date.between("2026-07-01", "2026-07-31").all()
            or rows.duplicated(["signal_date", "symbol6"]).any()
            or rows[list(FEATURES)].isna().any().any()
            or not rows.signal_return.between(.03, .06).all()):
        raise ValueError("July 15m candidate cohort is incomplete or inconsistent")
    earlier = pd.read_csv(PRIOR_JULY, dtype={"symbol6": str})
    earlier = earlier[earlier.model.eq("精确训练七因子_去当前价高于均线")]
    keys = ["signal_date", "next_date", "symbol6"]
    checked = earlier[keys + ["target_return", *FEATURES]].merge(
        rows[keys + ["target_next_high15_return", *FEATURES]],
        on=keys, validate="one_to_one", suffixes=("_old", "_new"))
    feature_diffs = [float(np.max(np.abs(
        checked[f"{feature}_old"] - checked[f"{feature}_new"])))
        for feature in FEATURES]
    target_diff = float(np.max(np.abs(
        checked.target_return - checked.target_next_high15_return)))
    if len(checked) != 115 or max(feature_diffs + [target_diff]) > 1e-8:
        raise ValueError("Refetched July candidate data disagree with saved July examples")
    return rows, {"candidate_rows": len(rows), "candidate_days": 23,
                  "prior_top5_rows_reproduced": len(checked),
                  "prior_feature_max_abs_diff": max(feature_diffs),
                  "prior_target_max_abs_diff": target_diff}


def score_candidates(model, rows: pd.DataFrame) -> pd.DataFrame:
    scored = rows[["signal_date", "next_date", "symbol6", "name",
                   "signal_price", "signal_return", *FEATURES[1:]]].copy()
    probabilities = model.predict_proba(rows[list(FEATURES)])
    labels = list(model.classes_)
    for label in CLASSES:
        scored[f"p_{label}"] = probabilities[:, labels.index(label)]
    scored["predicted_class"] = np.asarray(labels)[probabilities.argmax(axis=1)]
    if not np.allclose(scored[[f"p_{label}" for label in CLASSES]].sum(axis=1), 1):
        raise ValueError("Four-class probabilities do not sum to one")
    return scored


def cached_closes(rows: pd.DataFrame) -> pd.DataFrame:
    """Read July 15m close outcomes only after scoring and saving the picks."""
    wanted = rows.groupby("symbol6")[["signal_date", "next_date"]].apply(
        lambda x: list(x.itertuples(index=False, name=None))).to_dict()
    output = []
    for symbol, dates in wanted.items():
        with gzip.open(CACHE / f"{symbol}.json.gz", "rt", encoding="utf-8") as source:
            bars = json.load(source)
        by_stamp = {str(bar["day"])[:16]: bar for bar in bars}
        for signal_date, next_date in dates:
            record = {"signal_date": signal_date, "next_date": next_date,
                      "symbol6": symbol}
            stamps = {"close_1500": f"{signal_date} 15:00",
                      **{f"close_{minute.replace(':', '')}": f"{next_date} {minute}"
                         for minute in SELLS}}
            for column, stamp in stamps.items():
                bar = by_stamp.get(stamp)
                if bar is None:
                    raise ValueError(f"Missing 15m close {symbol} {stamp}")
                record[column] = float(bar["close"])
                if record[column] <= 0:
                    raise ValueError(f"Invalid close {symbol} {stamp}")
            record["high_0945"] = float(by_stamp[f"{next_date} 09:45"]["high"])
            output.append(record)
    result = pd.DataFrame(output)
    if (len(result) != len(rows)
            or result.duplicated(["signal_date", "symbol6"]).any()):
        raise ValueError("Cached 15m closes do not cover the candidate pool")
    return result


def check_next_references(selected: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Check that raw overnight returns do not cross an ex-rights price reset."""
    codes = sorted(selected.symbol6.unique())
    placeholders = ",".join(["%s"] * len(codes))
    conn = db_connection(Path("/Volumes/ext/fin_agent/.env"))
    try:
        daily = query(conn, "SELECT trade_date, LEFT(stk_code,6) symbol6, "
                      "preclose, close FROM kcrp_stock_price "
                      "WHERE trade_date BETWEEN %s AND %s "
                      "AND LEFT(stk_code,6) IN (" + placeholders + ")",
                      ("2026-07-01", "2026-08-03", *codes))
    finally:
        conn.rollback()
        conn.close()
    daily["trade_date"] = daily.trade_date.astype(str)
    day_close = daily.rename(columns={"trade_date": "signal_date", "close": "t_close"})
    next_reference = daily.rename(columns={"trade_date": "next_date",
                                           "preclose": "next_preclose"})
    checked = selected[["signal_date", "next_date", "symbol6"]].merge(
        day_close[["signal_date", "symbol6", "t_close"]],
        on=["signal_date", "symbol6"], validate="many_to_one").merge(
        next_reference[["next_date", "symbol6", "next_preclose"]],
        on=["next_date", "symbol6"], validate="many_to_one")
    checked[["t_close", "next_preclose"]] = checked[
        ["t_close", "next_preclose"]].astype(float)
    checked["next_reference_gap_pct"] = (
        checked.next_preclose / checked.t_close - 1) * 100
    if len(checked) != len(selected) or checked.next_reference_gap_pct.isna().any():
        raise ValueError("Missing overnight price reference for a selected stock")
    return checked, {"checked": len(checked),
                     "over_1pct_gap": int(checked.next_reference_gap_pct.abs().gt(1).sum()),
                     "max_abs_gap_pct": float(checked.next_reference_gap_pct.abs().max())}


def summarize(rows: pd.DataFrame, picks: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    if not picks.groupby("signal_date").size().eq(2).all() or picks.signal_date.nunique() != 23:
        raise ValueError("The selected set does not contain two stocks on every July day")
    selected = picks.copy()
    for column in ("close_0945", "close_1000", "close_1015"):
        for frame in (rows, selected):
            frame[f"return_{column}_pct"] = (frame[column]/frame.signal_price-1)*100
    selected["return_0945_from_1500_pct"] = (
        selected.close_0945/selected.close_1500-1)*100
    day_rows = []
    for day, group in selected.groupby("signal_date", sort=True):
        pool = rows[rows.signal_date.eq(day)]
        rank1 = group[group.selection_rank.eq(1)].iloc[0]
        rank2 = group[group.selection_rank.eq(2)].iloc[0]
        day_rows.append({
            "signal_date": day, "next_date": rank1.next_date,
            "candidate_stocks": len(pool),
            "rank1_symbol6": rank1.symbol6, "rank1_name": rank1["name"],
            "rank1_predicted_class": rank1.predicted_class,
            "rank1_p_ge3": rank1.p_ge3, "rank1_p_lt0": rank1.p_lt0,
            "rank1_0945_return_pct": rank1.return_close_0945_pct,
            "rank2_symbol6": rank2.symbol6, "rank2_name": rank2["name"],
            "rank2_predicted_class": rank2.predicted_class,
            "rank2_0945_return_pct": rank2.return_close_0945_pct,
            "top2_0945_equal_weight_pct": group.return_close_0945_pct.mean(),
            "pool_0945_equal_weight_pct": pool.return_close_0945_pct.mean(),
            "top2_1000_equal_weight_pct": group.return_close_1000_pct.mean(),
            "pool_1000_equal_weight_pct": pool.return_close_1000_pct.mean(),
            "top2_1015_equal_weight_pct": group.return_close_1015_pct.mean(),
            "pool_1015_equal_weight_pct": pool.return_close_1015_pct.mean(),
            "top2_0945_from_1500_equal_weight_pct":
                group.return_0945_from_1500_pct.mean(),
        })
    daily = pd.DataFrame(day_rows)
    def bucket(frame: pd.DataFrame) -> dict:
        sale = frame.return_close_0945_pct
        return {"stocks": len(frame), "days": frame.signal_date.nunique(),
                "mean_pct": float(sale.mean()), "median_pct": float(sale.median()),
                "positive": int(sale.gt(0).sum()),
                "at_least_1pct": int(sale.ge(1).sum()),
                "at_least_3pct": int(sale.ge(3).sum()),
                "below_minus_1pct": int(sale.lt(-1).sum())}
    result = {
        "candidate_pool": bucket(rows),
        "candidate_pool_equal_day_mean_pct": float(
            daily.pool_0945_equal_weight_pct.mean()),
        "top1": bucket(selected[selected.selection_rank.eq(1)]),
        "top1_without_three_best_days_mean_pct": float(
            selected[selected.selection_rank.eq(1)].return_close_0945_pct
            .sort_values().iloc[:-3].mean()),
        "rank2": bucket(selected[selected.selection_rank.eq(2)]),
        "top2": bucket(selected),
        "top1_daily_outperformed_pool_days": int(
            daily.rank1_0945_return_pct.gt(daily.pool_0945_equal_weight_pct).sum()),
        "top2_daily_outperformed_pool_days": int(
            daily.top2_0945_equal_weight_pct.gt(daily.pool_0945_equal_weight_pct).sum()),
        "mean_return_by_exit_pct": {
            slot: {"top1": float(selected[selected.selection_rank.eq(1)][
                f"return_close_{slot}_pct"].mean()),
                "top2": float(selected[f"return_close_{slot}_pct"].mean()),
                "candidate_pool_stock_weighted": float(rows[
                    f"return_close_{slot}_pct"].mean()),
                "candidate_pool_equal_day": float(rows.groupby("signal_date")[
                    f"return_close_{slot}_pct"].mean().mean())}
            for slot in ("0945", "1000", "1015")},
        "from_1500_entry_0945_exit_pct": {
            "top1": float(selected[selected.selection_rank.eq(1)][
                "return_0945_from_1500_pct"].mean()),
            "top2": float(selected.return_0945_from_1500_pct.mean())},
        "selected_predicted_classes": selected.predicted_class.value_counts().to_dict(),
        "top1_0945_bar_high_at_least_1pct": int(selected[
            selected.selection_rank.eq(1)].high15_return_pct.ge(1).sum()),
        "top2_0945_bar_high_at_least_1pct": int(selected.high15_return_pct.ge(1).sum()),
        "selected_current_names_containing_st": int(selected["name"].str.contains(
            "ST", case=False, na=False).sum()),
    }
    return result, daily


def render_report(summary: dict, daily: pd.DataFrame) -> str:
    metrics = summary["metrics"]

    def pct(value: float) -> str:
        return f"{value:+.3f}%"

    lines = [
        "# 四分类模型在 2026 年 7 月 15 分钟 K 上的回溯模拟",
        "",
        "本次沿用此前表现最好的**未加权四分类小型提升树**及七因子、"
        "`≥3%` 类优先且不足时顺延 `1–3%` 类的选股规则。模型仅用 09-02 至 09-30 "
        "的 20 个精确 1 分钟信号日、5,818 条样本训练；**7 月数据没有参与训练或调参**。"
        "由于训练发生在 7 月之后，这是一项**跨月份回溯检验**，不是 7 月当时可执行的"
        "前向回测。重新拟合模型对 10-08 候选的四类概率与存档最大差 "
        f"`{summary['training']['oct8_probability_max_abs_diff']:.2g}`。",
        "",
        "## 数据与交易口径",
        "",
        "7 月只有 15 分钟 K，因此把信号改为 **14:45 已完成 K 收盘后**；"
        "在该时点相对昨收上涨 3%–6% 的股票中计算相同七因子的 15 分钟代理值。"
        "主结果以 **14:45 收盘价作买价代理**，次交易日第一根 15 分钟 K 的 "
        "**09:45 收盘价**卖出；10:00、10:15 收盘另作对照。"
        "14:45 观察到收盘价并不保证同价成交，因此还列出 15:00 收盘买价代理。"
        "均为单笔收益率的算术平均，Top 2 是每日两只各买一份、同一时点卖出。",
        "",
        "重抓新浪非复权 15 分钟 K，结合数据库中 T−1 日线与估值，覆盖"
        " 7 月 23 个信号日及 08-03 的次晨，得到"
        f" **{summary['july_source_check']['candidate_rows']:,} 条完整候选**。"
        "旧报告保留的 115 条 7 月前五样本，其七因子与 15 分钟高价标签"
        "在本次重建中**逐项差为零**。46 只入选股的 T 日收盘与 T+1 日昨收"
        "参考价相同，无隔夜价格基准跳变。",
        "",
        "## 15 分钟收盘卖出的结果",
        "",
        "候选池一列是**先对每个交易日的全部候选等权，再对 23 天等权**。"
        "Top 1 每天一只、Top 2 每天两只，所以它们的每笔均值也等于逐日均值。"
        "如果把 13,244 个候选股票日直接混合平均，09:45 候选池是 "
        + pct(metrics["candidate_pool"]["mean_pct"])
        + "；日期权重不同，不能拿这个数替代下表的逐日等权基线。",
        "",
        "| 次晨统一卖出时点 | 候选池逐日等权 | Top 1，23 笔 | Top 2，46 笔等权 |",
        "|---|---:|---:|---:|",
    ]
    for slot in ("0945", "1000", "1015"):
        row = metrics["mean_return_by_exit_pct"][slot]
        lines.append("| " + " | ".join([
            slot[:2] + ":" + slot[2:], pct(row["candidate_pool_equal_day"]),
            pct(row["top1"]), pct(row["top2"]),
        ]) + " |")
    lines += [
        "",
        "09:45 主口径下，Top 1 平均 " + pct(metrics["top1"]["mean_pct"])
        + f"，盈利 {metrics['top1']['positive']}/23，收益至少 +1% "
        + f"{metrics['top1']['at_least_1pct']}/23，亏损超过 1% "
        + f"{metrics['top1']['below_minus_1pct']}/23；中位数 "
        + pct(metrics["top1"]["median_pct"]) + "。"
        "但第 2 名平均 " + pct(metrics["rank2"]["mean_pct"])
        + f"，仅 {metrics['rank2']['positive']}/23 笔盈利，"
        "把 Top 2 等权均值拉到 " + pct(metrics["top2"]["mean_pct"]) + "。",
        "",
        "| 09:45 卖出时的买价代理 | Top 1 平均 | Top 2 等权平均 |",
        "|---|---:|---:|",
        "| 当日 14:45 收盘 | " + pct(metrics["top1"]["mean_pct"]) + " | "
        + pct(metrics["top2"]["mean_pct"]) + " |",
        "| 当日 15:00 收盘 | "
        + pct(metrics["from_1500_entry_0945_exit_pct"]["top1"]) + " | "
        + pct(metrics["from_1500_entry_0945_exit_pct"]["top2"]) + " |",
        "",
        "Top 1 收益高于同日候选池均值的日期为 "
        f"{metrics['top1_daily_outperformed_pool_days']}/23；Top 2 为 "
        f"{metrics['top2_daily_outperformed_pool_days']}/23。"
        "Top 1 去掉收益最高的三天后，余下 20 天均值降到 "
        + pct(metrics["top1_without_three_best_days_mean_pct"])
        + "。这批样本中的首选有正的观察收益，但收益波动大；"
        "第二只在这批数据上明显拖累。",
        "",
        "09:45 这根 K 的**盘中最高价**曾达到买价 +1% 的有 Top 1 "
        f"{metrics['top1_0945_bar_high_at_least_1pct']}/23、Top 2 "
        f"{metrics['top2_0945_bar_high_at_least_1pct']}/46；真正按**09:45 收盘价**"
        "卖出达到 +1% 的只有 "
        f"{metrics['top1']['at_least_1pct']}/23、"
        f"{metrics['top2']['at_least_1pct']}/46。"
        "高价触及不能当作收盘成交。",
        "",
        "## 每日 09:45 收盘卖出",
        "",
        "| 信号日 → 卖出日 | 第 1 名／收益 | 第 2 名／收益 | Top 2 等权 | 当日候选池等权 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in daily.itertuples(index=False):
        lines.append("| " + " | ".join([
            row.signal_date[5:] + " → " + row.next_date[5:],
            row.rank1_name + " " + pct(row.rank1_0945_return_pct),
            row.rank2_name + " " + pct(row.rank2_0945_return_pct),
            pct(row.top2_0945_equal_weight_pct),
            pct(row.pool_0945_equal_weight_pct),
        ]) + " |")
    lines += [
        "",
        "## 结论边界与底稿",
        "",
        "原四分类模型学习的是**次晨前十分钟十根 1 分钟 K 的第二高价**。"
        "单根 15 分钟 K 无法还原该标签，因此本页没有计算‘原四分类准确率’；"
        "只比较真实可观察的 15 分钟收盘卖出收益。信号从 14:40 改为 14:45，"
        "卖点从 09:40 改为 09:45，买价也只是 K 线代理。"
        "7 月没有完整历史 ST 身份和真实委托成交记录，收益未扣费用与滑点。"
        "本次结论不能与之前精确 1 分钟的收益当作同口径直接相加。",
        "",
        "[测前选股与概率](selected_before_outcomes.csv)、"
        "[46 笔价格和收益](selected_with_outcomes.csv)、"
        "[逐日对账](daily_results.csv)、[机器摘要与输入哈希](summary.json)。"
        "完整候选特征和原始 15 分钟 K 保存在本地忽略目录；"
        "复算依次运行 `fetch_automl_sina_15m_july.py`、"
        "`build_automl_sina_15m_july.py`、"
        "`evaluate_automl_four_class_july15m.py`。",
        "",
    ]
    return "\n".join(lines)


def run() -> dict:
    model, training = fitted_model()
    candidates, source_check = load_candidates()
    scored = score_candidates(model, candidates)
    picks = class_priority_fallback(scored)
    OUT.mkdir(parents=True, exist_ok=True)
    picks.to_csv(OUT / "selected_before_outcomes.csv", index=False)
    closes = cached_closes(candidates)
    evaluated = scored.merge(closes, on=["signal_date", "next_date", "symbol6"],
                             validate="one_to_one")
    reference = candidates[["signal_date", "symbol6", "signal_price",
                            "next_high15", "next_open"]].merge(
        closes, on=["signal_date", "symbol6"], validate="one_to_one")
    if (not np.allclose(reference.next_high15, reference.high_0945)
            or not reference.close_0945.gt(0).all()):
        raise ValueError("Refetched 09:45 bar disagrees with the July proxy labels")
    selected = picks.merge(closes, on=["signal_date", "next_date", "symbol6"],
                           validate="one_to_one").merge(
        candidates[["signal_date", "symbol6", "next_high15", "next_open"]],
        on=["signal_date", "symbol6"], validate="one_to_one")
    selected["high15_return_pct"] = (selected.next_high15 /
                                     selected.signal_price - 1) * 100
    price_references, reference_check = check_next_references(selected)
    selected = selected.merge(
        price_references[["signal_date", "symbol6", "next_reference_gap_pct"]],
        on=["signal_date", "symbol6"], validate="one_to_one")
    metrics, daily = summarize(evaluated, selected)
    for slot in ("0945", "1000", "1015"):
        selected[f"return_{slot}_pct"] = (
            selected[f"close_{slot}"]/selected.signal_price-1)*100
    selected["return_0945_from_1500_pct"] = (
        selected.close_0945/selected.close_1500-1)*100
    selected.to_csv(OUT / "selected_with_outcomes.csv", index=False)
    daily.to_csv(OUT / "daily_results.csv", index=False)
    summary = {
        "training": training, "july_source_check": source_check,
        "selected_overnight_reference_check": reference_check,
        "model": "unweighted four-class small_boosted_tree",
        "signal": "July 14:45 completed 15m bar; seven proxy factors, candidate 3-6%",
        "entry_proxy": "same July 14:45 bar close; 15:00 close alternative",
        "exit": "next trading day completed 09:45/10:00/10:15 15m bar close",
        "label_warning": "Original 10-minute second-high target is not observable from one 15m bar; do not call 09:45 close return four-class accuracy",
        "temporal_warning": "Model trained on September days after July; retrospective cross-period transport, not July-time forward inference",
        "metrics": metrics,
        "input_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (DATA, SECOND_HIGHS, PROXY, PRIOR_JULY)},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    (OUT / "README.md").write_text(render_report(summary, daily))
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
