"""Apply the frozen R86 D1 Q75 risk model to the corrected QDC B2 trades.

QDC supplies daily prices and D0/D1 minute OHLCV. Historical turnover,
board streak, and valuation are read from the original D0 panel / KingdomAI
because QDC does not contain those fields. No model fit or threshold search.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd

from scripts import research_b2_qdc_r104 as r104
from scripts import research_b2_qdc_r105 as r105
from scripts.replay_limit_rebound_cash_r23 import replay
from scripts.research_b2_daily_only_r100 import read_inputs

ROOT = r104.ROOT
OUT = ROOT / "outputs/stock_automl/b2_qdc_r106_20261024"
RISK = ROOT / "docs/stock_automl_runs/20261021_b2_d1_risk"
NEWS = ROOT / "docs/stock_automl_runs/20261022_b2_news_factor_r74/709候选_新闻因子.csv"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def selected() -> pd.DataFrame:
    signals = pd.read_pickle(r105.OUT / "signals.pkl")
    gates = pd.read_csv(r105.OUT / "gates.csv")
    paused = set(gates.loc[gates.paused, "model_month"])
    frame = signals[signals.d0_date.ge("2022-05-01") & ~signals.model_month.isin(paused)].copy()
    assert not frame.duplicated(["stk_code", "d0_date"]).any()
    return frame.sort_values(["d0_date", "stk_code"]).reset_index(drop=True)


def d0_minutes(frame: pd.DataFrame) -> pd.DataFrame:
    dest = OUT / "qdc_selected_d0_minutes.pkl"
    if dest.exists():
        return pd.read_pickle(dest)
    wanted = frame[["qdc_symbol", "d0_date"]].drop_duplicates()
    parts = []
    for year in sorted(wanted.d0_date.str[:4].astype(int).unique()):
        con = duckdb.connect()
        con.register("wanted", wanted[wanted.d0_date.str.startswith(str(year))])
        path = (str(r104.QDC / "1分钟" / f"{year}.parquet") if year < 2026
                else str(r104.QDC / "1分钟/2026-*.parquet"))
        bars = con.execute("""
            SELECT m.symbol AS qdc_symbol,
              CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS DATE) AS VARCHAR) AS d0_date,
              CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIMESTAMP) AS time,
              m.open, m.high, m.low, m.close, m.volume, m.amount, m.source
            FROM read_parquet(?) m JOIN wanted w ON m.symbol=w.qdc_symbol
              AND CAST(CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai'
                  AS DATE) AS VARCHAR)=w.d0_date
            WHERE CAST(to_timestamp(m.ts/1000) AT TIME ZONE 'Asia/Shanghai' AS TIME)
              BETWEEN TIME '09:30:00' AND TIME '15:00:00'
        """, [path]).df()
        con.close()
        parts.append(bars)
        print(f"D0 minutes {year}: {len(bars)}", flush=True)
    result = pd.concat(parts, ignore_index=True)
    result.to_pickle(dest)
    return result


def valuation(frame: pd.DataFrame) -> pd.DataFrame:
    dest = OUT / "d0_valuation_readonly.csv"
    if dest.exists():
        return pd.read_csv(dest, dtype={"stk_code": str})
    from scripts.benchmark_automl_1440_inference import db_connection, query
    keys = list(zip(frame.stk_code, frame.d0_date))
    rows = []
    with db_connection() as conn:
        for i in range(0, len(keys), 50):
            batch = keys[i:i + 50]
            marks = ",".join(["(%s,%s)"] * len(batch))
            part = query(conn, f"SELECT stk_code,trade_date,total_mv,total_share,update_time "
                         f"FROM kcrp_stock_pricevaluate WHERE (stk_code,trade_date) IN ({marks})",
                         tuple(v for pair in batch for v in pair))
            rows.extend(part.to_dict("records"))
        found = {(str(x["stk_code"]), str(x["trade_date"])) for x in rows}
        for code, day in keys:
            if (code, day) in found:
                continue
            prior = query(conn, "SELECT stk_code,trade_date,total_share,update_time "
                          "FROM kcrp_stock_pricevaluate WHERE stk_code=%s AND trade_date<=%s "
                          "AND total_share>0 ORDER BY trade_date DESC LIMIT 1", (code, day))
            if len(prior):
                record = prior.iloc[0].to_dict()
                record["requested_d0"] = day
                rows.append(record)
        conn.rollback()
    result = pd.DataFrame(rows)
    result.to_csv(dest, index=False)
    return result


def normalize_d0(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    raw = raw.sort_values("time").copy()
    at30 = raw[raw.time.dt.strftime("%H:%M").eq("09:30")]
    at31 = raw[raw.time.dt.strftime("%H:%M").eq("09:31")]
    if len(at30) == len(at31) == 1:
        first = at31.iloc[0].copy()
        first["open"] = at30.open.iloc[0]
        first["high"] = max(at30.high.iloc[0], first.high)
        first["low"] = min(at30.low.iloc[0], first.low)
        first["volume"] += at30.volume.iloc[0]
        first["amount"] += at30.amount.iloc[0]
        rest = raw[~raw.time.dt.strftime("%H:%M").isin(["09:30", "09:31"])]
        bars = pd.concat([pd.DataFrame([first]), rest], ignore_index=True)
    else:
        bars = raw[~raw.time.dt.strftime("%H:%M").eq("09:30")].copy()
    return bars, dict(raw_bars=len(raw), completed_bars=len(bars), has30=len(at30) == 1,
                      has31=len(at31) == 1)


def d0_shape(bars: pd.DataFrame, cap: float) -> dict:
    touch = bars.high.ge(cap - .001)
    below = bars.low.lt(cap - .001)
    close_sealed = bars.close.ge(cap - .001)
    prior_touch = touch.cummax().shift(fill_value=False)
    definite = (prior_touch & below) | (bars.open.ge(cap - .001) & below) | (touch & bars.close.lt(cap - .001))
    ambiguous = bool((touch & below & ~prior_touch & bars.open.lt(cap - .001) & close_sealed).any())
    opened = "是" if definite.any() else ("分钟内顺序不明" if ambiguous else "否")
    total = bars.amount.sum()
    tail = bars.loc[bars.time.dt.strftime("%H:%M").ge("14:31"), "amount"].sum()
    return dict(opened_after_touch=opened, close_sealed_minutes=int(close_sealed.sum()),
                last30_amount_fraction=tail / total if total else np.nan)


def early_features(row: pd.Series, minute: pd.DataFrame) -> dict:
    q = minute[minute.time.dt.strftime("%H:%M").isin(["09:31", "09:32", "09:33", "09:34"])].sort_values("time")
    if len(q) != 4:
        return {}
    pre = float(row.qdc_d0_close)
    o, c, h, l = float(q.open.iloc[0]), float(q.close.iloc[-1]), float(q.high.max()), float(q.low.min())
    v, cl = q.volume.to_numpy(float), q.close.to_numpy(float)
    ratio = lambda a, b: float(a / b) if b else np.nan
    return dict(open_gap=o / pre - 1, body5=c / o - 1, high5=h / pre - 1,
                low5=l / pre - 1, pullback5=(h - c) / pre,
                position5=ratio(c - l, h - l), early_volume_ratio=ratio(v.sum(), row.qdc_d0_volume),
                early_volume_accel=ratio(v[2:].sum(), v[:2].sum()),
                early_down_volume=ratio(v[q.close.lt(q.open)].sum(), v.sum()),
                early_up_count=int(q.close.gt(q.open).sum()),
                early_slope=float(np.polyfit(np.arange(4), cl / pre, 1)[0]),
                early_path=float(np.abs(np.diff(np.r_[o, cl])).sum() / pre),
                early_below_d0=float((cl < pre).mean()),
                early_drawdown=float((cl / np.maximum.accumulate(np.r_[o, cl])[1:] - 1).min()),
                early_upper=(h - max(o, c)) / pre, early_lower=(min(o, c) - l) / pre)


def features(frame: pd.DataFrame, raw_d0: pd.DataFrame, early: pd.DataFrame,
             values: pd.DataFrame, base_panel: Path | None = None) -> pd.DataFrame:
    base = pd.read_pickle(base_panel or ROOT / "outputs/stock_automl/b2_2021_minute_r60_20261020/panel.pkl")
    base = base[["stk_code", "d0_date", "d0_turnover_pct", "board_streak_days",
                 "d0_normal_volume_ratio", "d0_cap"]]
    result = frame.merge(base, on=["stk_code", "d0_date"], how="left", validate="one_to_one")
    # Recent listings can lack a pre-board volume baseline. The frozen model
    # learned a train-only median imputer for that optional numeric feature.
    assert result[["d0_turnover_pct", "board_streak_days", "d0_cap"]].notna().all().all()
    result["d0_turnover_pct"] /= 100
    values = values.copy()
    values["trade_date"] = values.trade_date.astype(str)
    values["requested_d0"] = values.requested_d0.fillna(values.trade_date).astype(str)
    assert not values.duplicated(["stk_code", "requested_d0"]).any()
    vmap = values.set_index(["stk_code", "requested_d0"]).to_dict("index")
    d0_groups = {(s, d): group.sort_values("time") for (s, d), group in raw_d0.groupby(["qdc_symbol", "d0_date"])}
    d1_groups = {(s, d): group.sort_values("time") for (s, d), group in early.groupby(["qdc_symbol", "d1_date"])}
    rows = []
    for row in result.itertuples(index=False):
        data = row._asdict()
        day = d0_groups.get((row.qdc_symbol, row.d0_date))
        if day is None:
            raise ValueError(f"missing D0 minutes: {row.stk_code} {row.d0_date}")
        bars, bar_audit = normalize_d0(day)
        shape = d0_shape(bars, row.d0_cap)
        item = vmap.get((row.stk_code, row.d0_date))
        if item is None:
            data["total_mv_yi"] = np.nan
            data["mv_source"] = "missing"
        elif pd.notna(item.get("total_mv")) and item["trade_date"] == row.d0_date:
            data["total_mv_yi"] = float(item["total_mv"]) / 1e8
            data["mv_source"] = "D0 historical value"
        else:
            data["total_mv_yi"] = float(item["total_share"]) * row.qdc_d0_close / 1e8
            data["mv_source"] = "prior shares x D0 QDC close"
        data.update(shape)
        data.update(bar_audit)
        data["d0_ohlc_match"] = bool(np.allclose(
            [bars.open.iloc[0], bars.high.max(), bars.low.min(), bars.close.iloc[-1]],
            [row.qdc_d0_open, row.qdc_d0_high, row.qdc_d0_low, row.qdc_d0_close],
            atol=.011, rtol=0))
        data["d0_volume_ratio"] = bars.volume.sum() / row.qdc_d0_volume if row.qdc_d0_volume else np.nan
        first = d1_groups.get((row.qdc_symbol, row.d1_date))
        if first is None:
            raise ValueError(f"missing D1 first four: {row.stk_code} {row.d1_date}")
        data.update(early_features(pd.Series(data), first))
        rows.append(data)
    return pd.DataFrame(rows)


def score(frame: pd.DataFrame) -> pd.DataFrame:
    selection = json.loads((RISK / "selection_frozen.json").read_text())
    rule = next(x["rule"] for x in selection["candidates"] if x["id"] == "union_early_lr01")
    model_file = RISK / rule["file"]
    assert digest(model_file) == selection["models"]["union_early_lr01"]["sha256"]
    model = joblib.load(model_file)
    old_panel = pd.read_pickle(RISK / "panel.pkl")
    old_score = model.predict_proba(old_panel[rule["columns"]])[:, 1]
    assert int((old_score > rule["threshold"]).sum()) == 97
    result = frame.copy()
    # The frozen pipeline imputes numeric NaNs (e.g. flat four-bar ranges).
    assert result[["total_mv_yi", "d0_turnover_pct", "board_streak_days", "open_gap", "body5"]].notna().all().all()
    result["q75_score"] = model.predict_proba(result[rule["columns"]])[:, 1]
    result["q75_veto"] = result.q75_score.gt(rule["threshold"])
    news = pd.read_csv(NEWS, dtype={"stk_code": str})[["stk_code", "d0_date", "theme_denial_5"]]
    result = result.merge(news, on=["stk_code", "d0_date"], how="left", validate="one_to_one")
    completed = OUT / "missing_news_decisions.csv"
    if completed.exists():
        extra = pd.read_csv(completed, dtype={"stk_code": str})
        assert extra.official_complete.all() and extra.theme_denial_5.notna().all()
        result = result.merge(extra[["stk_code", "d0_date", "theme_denial_5"]],
                              on=["stk_code", "d0_date"], how="left", validate="one_to_one",
                              suffixes=("", "_extra"))
        result["theme_denial_5"] = result.theme_denial_5.fillna(result.theme_denial_5_extra)
        result = result.drop(columns="theme_denial_5_extra")
    result["news_veto"] = result.theme_denial_5.eq(1)
    result["news_known"] = result.theme_denial_5.notna()
    return result


def account(frame: pd.DataFrame) -> pd.DataFrame:
    _, calendar, _ = read_inputs()
    rows = []
    for label, mask in [("V2", pd.Series(True, index=frame.index)),
                        ("Q75", ~frame.q75_veto),
                        ("Q75+news", ~(frame.q75_veto | frame.news_veto))]:
        subset = frame[mask].copy()
        for yr in range(2022, 2027):
            part = subset[subset.d0_date.str[:4].eq(str(yr))]
            if part.empty:
                continue
            result, _, _, _, excluded = replay(part, calendar, start=f"{yr}-01-01",
                                               end=part.d2_date.max(), fee_rate=.001)
            assert not excluded
            rows.append(dict(rule=label, period=str(yr), buys=len(part),
                             return_pct=result["total_return"] * 100,
                             drawdown_pct=result["max_drawdown"] * 100))
        result, _, _, _, excluded = replay(subset, calendar, start="2022-01-01",
                                           end=subset.d2_date.max(), fee_rate=.001)
        assert not excluded
        rows.append(dict(rule=label, period="2022-26_continuous", buys=len(subset),
                         return_pct=result["total_return"] * 100,
                         drawdown_pct=result["max_drawdown"] * 100))
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = selected()
    raw_d0 = d0_minutes(frame)
    early = pd.read_pickle(r105.OUT / "qdc_candidate_first10_normalized.pkl")
    values = valuation(frame)
    panel = features(frame, raw_d0, early, values)
    panel = score(panel)
    panel.to_pickle(OUT / "risk_panel.pkl")
    columns = ["stk_code", "d0_date", "d1_date", "d2_date", "entry", "qdc_d2_close",
               "total_mv_yi", "mv_source", "d0_turnover_pct", "opened_after_touch",
               "q75_score", "q75_veto", "news_known", "news_veto", "d0_ohlc_match", "d0_volume_ratio"]
    panel[columns].to_csv(OUT / "risk_decisions.csv", index=False)
    report = account(panel)
    report.to_csv(OUT / "account_close.csv", index=False)
    inherited_sources = json.loads((r104.OUT / "manifest.json").read_text())["qdc_files"]
    manifest = dict(parent="b2_qdc_r105_20261024", risk_model_sha256=digest(RISK / "union_early_lr01.joblib"),
                    risk_selection_sha256=digest(RISK / "selection_frozen.json"),
                    news_source_sha256=digest(NEWS),
                    missing_news_evidence_sha256=(digest(OUT / "missing_news_evidence.json") if
                                                  (OUT / "missing_news_evidence.json").exists() else None),
                    d0_valuation_readonly_sha256=digest(OUT / "d0_valuation_readonly.csv"),
                    signals=len(panel),
                    q75_veto=int(panel.q75_veto.sum()), news_known=int(panel.news_known.sum()),
                    news_unknown=int((~panel.news_known).sum()), news_veto=int(panel.news_veto.sum()),
                    d0_ohlc_mismatch=int((~panel.d0_ohlc_match).sum()),
                    valuation_missing=int(panel.total_mv_yi.isna().sum()),
                    qdc_files={path: value for path, value in inherited_sources.items() if any(
                        f"/{year}" in path for year in range(2022, 2027))})
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(report.to_string(index=False), flush=True)
    print(manifest, flush=True)


if __name__ == "__main__":
    main()
