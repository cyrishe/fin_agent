"""B2 business rules: frozen R18 daily RF and R24 four-bar delayed simulation.

Minute times are END labels. This module never invents intraminute ordering,
substitutes a rejected winner, or treats a simulated price as a broker fill.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.experiment_limit_rebound_segments_r18 import FEATURES, segment_one
from scripts.experiment_limit_rebound_short_mixed_r12 import add_features, enrich_outcomes
from scripts.experiment_automl_limit_rebound_d0_open_r5 import build_panel, cap_price
from scripts.experiment_limit_rebound_four_minutes_r24 import first_features
from scripts.experiment_limit_rebound_shape_r19 import mask


STRATEGY_ID = "B2"
EXIT_RULE = "d2_trade_day_close"
FEATURE_ORDER = FEATURES["segments"]
TITLE = "B2｜D0涨停前三·D1四K形态·延后一K模拟买入"
DESCRIPTION = (
    "D0收盘封板股票按已冻结的51因子随机森林三日再板分数排序，保存原Top3；"
    "D1仅观察历史状态N/R的候选，前四根完整分钟K（09:31—09:34结束）计算形态，"
    "采用当月事先冻结的简单规则。09:35—09:40结束的分钟K逐根判断："
    "开盘价低于涨停价0.005元且该分钟有成交即激活；最早分钟优先，同分钟D0排名优先。"
    "锁定唯一股票后，四分实体须严格大于−8%；不通过则当日轮空、不替补。"
    "通过后至少等下一根K，在同股首次再次满足上述条件的分钟开盘价记模拟买入，"
    "最迟使用09:40结束的K；超时轮空、不换股。actionable初始为0，只有最终模拟买入的一只变1。"
    "所有Top3的开板分钟、形态与确认时间均回写；K内低于涨停只表示该分钟打开过，不能推断秒级时刻。"
    "D2（D1下一市场交易日）收盘记模拟卖出。买卖各0.1%用于净收益；"
    "资金基准100万元，早晨空仓用可用现金一半、有昨日持仓则用剩余全部现金，允许零碎股。"
    "模拟记录使用分钟开盘价代理，完成K后才可确认，entry_at与confirmed_at分开；不下单、不声称当时可全额成交。"
)


def definition() -> dict:
    return {
        "title": TITLE, "summary": DESCRIPTION,
        "training_method": (
            "固定RF240棵、深度7、叶最少50、max_features=0.8、random_state=20261013，"
            "中位数填充和缺失指示、正负类平衡权重，sklearn1.7.1。每个目标月用前第8至第3月共6个月训练、"
            "前2月验证，不合并验证重拟合；标签D1—D3任一天收盘封板，"
            "历史拟合样本限D1开盘低于涨停且完整标签。51个特征只用D0及以前：板前连续非板区、"
            "当轮截至D0连板区（含连板天数）及市场特征。形态79条预设规则至多2条件，"
            "用历史因果折外Top1六个月与两个月验证按R24原保留率、命中提升、收益约束、"
            "三个2月块稳定性和Wilson评分选定，迁移至Top3，未过关则不筛形态。"
            "每月冻结新模型、规则、样本、分数和哈希；每天只用该月模型推理。"
        ),
        "selection_formula": "D0所有原口径标准封板候选，score降序、代码升序，原排名1—3；不因D1淘汰补排名4以后。",
        "execution_formula": DESCRIPTION,
        "formula": {
            "version": "B2_R18_R24_four_fit_delay_20261015",
            "historical_reference": "R24/four_fit_delay",
            "features": FEATURE_ORDER, "target": "D1_D3_any_sealed_close",
            "train_months": 6, "validation_months": 2, "refit_validation": False,
            "top_k": 3, "observation_bars": 4,
            "activation_bar_end": ["09:35", "09:40"],
            "entry_delay_bars": 1, "entry_last_bar_end": "09:40",
            "body_gate": "> -0.08 + 1e-12", "replacement_after_choice": False,
            "price_tolerance": 0.005, "exit_rule": EXIT_RULE,
            "fee_per_side": 0.001, "initial_cash": 1000000,
            "cash_allocation": "flat_half_cash_else_all_cash",
            "simulation_only": True, "confirmation": "completed_minute",
            "first_stage_status": "Exclude S/Y only, reproduce legacy R18 ranking",
            "execution_status": "D0 and D1 historical N/R only",
            "reference_metrics": {
                "period": "2026-01-05/2026-10-14", "entries": 122,
                "return": 1.1659492677691263, "drawdown": 0.20788524829471644,
                "note": "历史已研究、含1笔待卖估值；模拟量价，不保证实盘收益/成交。",
            },
        },
    }


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def historical_states(keys: pd.DataFrame, intervals: pd.DataFrame) -> pd.DataFrame:
    h = intervals.copy()
    h["symbol"] = h.stk_code.astype(str).str[:6]
    for col in ("begin_date", "end_date"):
        h[col] = h[col].astype(str)
    groups = {s: g for s, g in h.groupby("symbol")}
    rows = []
    for sym, day in keys[["symbol", "asof_date"]].dropna().drop_duplicates().itertuples(index=False):
        g = groups.get(sym)
        a = g[g.begin_date.le(day) & g.end_date.gt(day)] if g is not None else []
        if len(a) != 1:
            raise ValueError(f"历史状态不唯一: {sym} {day}")
        kind = a.st_type.iloc[0]
        rows.append({"symbol": sym, "asof_date": day, "st_type": kind,
                     "is_st": int(kind in ("S", "Y"))})
    return pd.DataFrame(rows, columns=["symbol", "asof_date", "st_type", "is_st"])


def build_features(daily: pd.DataFrame, intervals: pd.DataFrame, out: Path,
                   start: str) -> pd.DataFrame:
    """Same historical formulas, with a freshly resolved as-of status cache."""
    out.mkdir(parents=True, exist_ok=True)
    days = sorted(daily.trade_date.unique())
    next_day = dict(zip(days, days[1:]))
    boards = daily[daily.up & daily.trade_date.ge(start)]
    keys = pd.concat([
        boards[["symbol", "trade_date"]].rename(columns={"trade_date": "asof_date"}),
        boards[["symbol", "trade_date"]].assign(trade_date=boards.trade_date.map(next_day))
            .rename(columns={"trade_date": "asof_date"}),
    ])
    historical_states(keys, intervals).to_csv(out / "historical_st.csv.gz", index=False, compression="gzip")
    panel, _ = build_panel(daily, out, score_all_d0=True, d0_start=start)
    panel = add_features(panel, daily)
    panel = enrich_outcomes(panel, daily)
    panel["first_i"] = panel.first_limit_date.map({d: i for i, d in enumerate(days)})
    targets = {s: g for s, g in panel.groupby("symbol")}
    rows = []
    for sym, g in daily.groupby("symbol", sort=False):
        if sym not in targets:
            continue
        pos = {d: i for i, d in enumerate(g.trade_date)}
        for r in targets[sym].itertuples():
            values, meta, _ = segment_one(g, pos[r.first_limit_date], pos[r.d0_date])
            if meta["raw_board_days"] != r.board_streak_days:
                raise ValueError("连板区长度不一致")
            rows.append({"symbol": sym, "d0_date": r.d0_date, **values})
    extra = pd.DataFrame(rows, columns=["symbol", "d0_date", *[c for c in FEATURE_ORDER if c not in panel]])
    return panel.merge(extra, on=["symbol", "d0_date"], validate="one_to_one")


def valid_bars(bars: pd.DataFrame, day: str, as_of: datetime, cap: float) -> pd.DataFrame:
    """Only completed, internally valid, same-day bars; no later OHLC audit."""
    if bars.empty:
        return bars.copy()
    f = bars.copy()
    f["time"] = pd.to_datetime(f.time)
    cutoff = pd.Timestamp(as_of).tz_localize(None) if as_of.tzinfo else pd.Timestamp(as_of)
    f = f[f.time.dt.strftime("%Y-%m-%d").eq(day) & f.time.le(cutoff)
          & f.time.dt.strftime("%H:%M").between("09:31", "09:40")].sort_values("time")
    if f.time.duplicated().any():
        raise ValueError("分钟时间重复")
    cols = ["open", "high", "low", "close", "volume"]
    f[cols] = f[cols].apply(pd.to_numeric)
    if (not np.isfinite(f[cols].to_numpy()).all() or f[cols[:4]].le(0).any().any()
            or f.volume.lt(0).any() or f.high.gt(cap + .011).any()
            or f.high.lt(f[["open", "close", "low"]].max(axis=1) - .005).any()
            or f.low.gt(f[["open", "close", "high"]].min(axis=1) + .005).any()):
        raise ValueError("分钟价格或成交量不合法")
    return f


def evaluate_morning(picks: list[dict], bars: dict[str, pd.DataFrame], day: str,
                     as_of: datetime, rule: dict) -> list[dict]:
    """Deterministic prefix replay. All contender bars must arrive before tie resolution.

    fields supplied per pick: symbol6, selection_rank, preclose, cap, ordinary.
    Recomputing with an extended prefix preserves the locked winner. Missing
    contender evidence pauses that minute instead of fabricating a later winner.
    """
    result = {p["symbol6"]: {**p, "actionable": 0, "shape_ready": False,
                              "shape_pass": False, "chosen": False} for p in picks}
    ready = {}
    for p in picks:
        code = p["symbol6"]; z = result[code]
        if not p["ordinary"]:
            z["reason"] = "D0或D1不是N/R"; continue
        b = valid_bars(bars.get(code, pd.DataFrame()), day, as_of, p["cap"])
        if len(b):
            opened = b[b.volume.gt(0) & b.low.lt(p["cap"] - .005)]
            if len(opened):
                z["first_open_bar_end"] = opened.time.iloc[0].isoformat()
        first = b[pd.to_datetime(b.time).dt.strftime("%H:%M").le("09:34")] if len(b) else b
        if len(first) != 4 or first.time.dt.strftime("%H:%M").tolist() != ["09:31", "09:32", "09:33", "09:34"]:
            z["reason"] = "等待前四根完整K"; continue
        f = first_features(first, p["preclose"], p["cap"], 4)
        z.update(shape_ready=True, shape_pass=bool(mask(pd.DataFrame([f]), rule).iloc[0]), shape_features=f)
        ready[code] = b.set_index(b.time.dt.strftime("%H:%M"))
    # Missing shape of any ordinary original Top3 prevents silently dropping it.
    if any(p["ordinary"] and not result[p["symbol6"]]["shape_ready"] for p in picks):
        return list(result.values())
    contenders = sorted([p for p in picks if result[p["symbol6"]]["shape_pass"]], key=lambda p: p["selection_rank"])
    winner = None; activated = None
    for n in range(35, 41):
        label = f"09:{n:02}"
        stamp = datetime.fromisoformat(f"{day} {label}:00")
        local_now = as_of.replace(tzinfo=None)
        if stamp > local_now:
            break
        if winner is None:
            if any(label not in ready[p["symbol6"]].index for p in contenders):
                break
            for p in contenders:
                row = ready[p["symbol6"]].loc[label]
                if row.volume > 0 and row.open < p["cap"] - .005:
                    winner = p; activated = n
                    z = result[p["symbol6"]]
                    z.update(chosen=True, activation_bar_end=stamp.isoformat(),
                             activation_at=(stamp-timedelta(minutes=1)).isoformat())
                    if z["shape_features"]["body5"] <= -.08 + 1e-12:
                        z["reason"] = "四分实体未超过-8%，当日不替补"
                        return list(result.values())
                    break
        elif n > activated:
            code = winner["symbol6"]
            if label not in ready[code].index:
                break
            row = ready[code].loc[label]
            if row.volume > 0 and row.open < winner["cap"] - .005:
                result[code].update(actionable=1, entry_price=float(row.open),
                    entry_at=(stamp-timedelta(minutes=1)).isoformat(),
                    entry_bar_end=stamp.isoformat(), reason="按回测规则模拟买入")
                break
    if winner is not None and not result[winner["symbol6"]]["actionable"]:
        result[winner["symbol6"]]["reason"] = "等待同股下一根合格K" if as_of.strftime("%H:%M") < "09:40" else "窗口结束，无延后买点，不替补"
    return list(result.values())
