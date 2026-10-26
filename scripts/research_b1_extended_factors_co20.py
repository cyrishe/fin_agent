"""CO20: extend the frozen B1R OOF replay and screen causal stock factors."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "outputs/stock_automl/b1_market_gate_co14_20261017/samples.csv.gz"
B1R = ROOT / "outputs/stock_automl/b1_history_20261016/run"
CO17 = ROOT / "outputs/stock_automl/b1_validation_co17_20261026/daily.csv"
FUND = ROOT / "outputs/stock_automl/rsi_model_kp3_20261016/fundflow_source"
OUT = ROOT / "docs/stock_automl_runs/20261026_b1_extended_co20"
PERIODS = {
    "discovery_2025": ("2025-08-01", "2025-12-31"),
    "validation_2026H1": ("2026-01-01", "2026-06-30"),
    "check_2026Q3": ("2026-07-01", "2026-09-30"),
    "all": ("2025-08-01", "2026-09-30"),
}
GATES = [
    "ret5_up", "ret5_down", "ret20_up", "ret20_down",
    "position20_high", "position20_low", "position60_high", "position60_low",
    "fund1_in", "fund1_out", "fund5_in", "fund5_out",
    "ret5_up_position60_low", "ret5_up_fund5_in",
]
FACTORS = ["ret5", "ret20", "position20", "position60", "fund1", "fund5"]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for part in iter(lambda: file.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def price_history(codes: set[str]) -> tuple[dict[str, pd.Series], list[str]]:
    parts, dates = [], set()
    for path in (B1R / "older_adjusted.csv.gz", B1R / "daily.csv.gz"):
        for chunk in pd.read_csv(path, dtype={"symbol6": str},
                                 usecols=["date", "symbol6", "adjclose"],
                                 chunksize=100_000):
            dates.update(chunk.date.unique())
            parts.append(chunk[chunk.symbol6.isin(codes)])
    frame = pd.concat(parts, ignore_index=True).drop_duplicates(["symbol6", "date"])
    frame = frame.sort_values(["symbol6", "date"])
    return {code: rows.set_index("date").adjclose for code, rows in frame.groupby("symbol6")}, sorted(dates)


def fund_history(codes: set[str]) -> tuple[pd.DataFrame, list[Path]]:
    parts, sources = [], []
    for path in sorted(FUND.glob("20??-??.pkl.gz")):
        if not "2025-01" <= path.name[:7] <= "2026-09":
            continue
        d = pd.read_pickle(path)
        d["symbol6"] = d.stk_code.str[:6]
        d = d[d.symbol6.isin(codes)].copy()
        d["date"] = pd.to_datetime(d.trade_date).dt.strftime("%Y-%m-%d")
        parts.append(d[["symbol6", "date", "main_buy_value", "main_sell_value"]])
        sources.append(path)
    frame = pd.concat(parts).drop_duplicates(["symbol6", "date"])
    return frame.set_index(["symbol6", "date"]), sources


def features(base: pd.DataFrame) -> tuple[pd.DataFrame, list[Path]]:
    codes = set(base.symbol6.dropna())
    prices, calendar = price_history(codes)
    calix = {day: i for i, day in enumerate(calendar)}
    funds, fund_paths = fund_history(codes)
    rows = []
    for row in base.itertuples():
        result = {"signal_date": row.signal_date, "symbol6": row.symbol6}
        if not isinstance(row.symbol6, str):
            rows.append(result)
            continue
        i = calix[row.signal_date]
        history = prices[row.symbol6]
        for n in (5, 20):
            ds = calendar[i - n - 1:i]
            values = history.reindex(ds).to_numpy(dtype=float)
            result[f"ret{n}"] = (values[-1] / values[0] - 1
                                  if len(values) == n + 1 and np.isfinite(values).all()
                                  and (values > 0).all() else np.nan)
        for n in (20, 60):
            ds = calendar[i - n:i]
            values = history.reindex(ds).to_numpy(dtype=float)
            spread = np.ptp(values) if len(values) == n else np.nan
            result[f"position{n}"] = ((values[-1] - values.min()) / spread
                                       if np.isfinite(values).all() and spread > 0 else np.nan)
        for n in (1, 5):
            ds = calendar[i - n:i]
            keys = pd.MultiIndex.from_tuples([(row.symbol6, d) for d in ds],
                                             names=["symbol6", "date"])
            f = funds.reindex(keys)
            buy, sell = f.main_buy_value.to_numpy(float), f.main_sell_value.to_numpy(float)
            valid = (len(ds) == n and np.isfinite(buy).all() and np.isfinite(sell).all()
                     and (buy >= 0).all() and (sell >= 0).all() and (buy + sell).sum() > 0)
            result[f"fund{n}"] = (float((buy.sum() - sell.sum()) / (buy.sum() + sell.sum()))
                                   if valid else np.nan)
        rows.append(result)
    f = pd.DataFrame(rows)
    assert len(f) == len(base)
    return base.merge(f, on=["signal_date", "symbol6"], validate="one_to_one"), fund_paths


def gate_columns(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["ret5_up"] = d.ret5 > 0
    d["ret5_down"] = d.ret5 <= 0
    d["ret20_up"] = d.ret20 > 0
    d["ret20_down"] = d.ret20 <= 0
    for n in (20, 60):
        d[f"position{n}_high"] = d[f"position{n}"] >= .5
        d[f"position{n}_low"] = d[f"position{n}"] < .5
    for n in (1, 5):
        d[f"fund{n}_in"] = d[f"fund{n}"] > 0
        d[f"fund{n}_out"] = d[f"fund{n}"] <= 0
    d["ret5_up_position60_low"] = d.ret5_up & d.position60_low
    d["ret5_up_fund5_in"] = d.ret5_up & d.fund5_in
    return d


def drawdown(returns: pd.Series) -> float:
    path = np.r_[1., np.cumprod(1 + returns.fillna(0).to_numpy(float))]
    return float(100 * np.max(1 - path / np.maximum.accumulate(path)))


def evaluate(d: pd.DataFrame, gate: str) -> dict:
    selected = d[d.executed & (d[gate] if gate != "all" else True)]
    blocked = d[d.executed & ~(d[gate] if gate != "all" else pd.Series(True, index=d.index))]
    ret = d.net.where(d.executed & (d[gate] if gate != "all" else True), 0.)
    by_month = (1 + ret).groupby(d.month).prod() - 1
    nav = float(np.prod(1 + ret) - 1)
    no_best = float(np.prod(1 + by_month.drop(by_month.idxmax())) - 1)
    return {
        "days": len(d), "trades": len(selected), "blocked": len(blocked),
        "net_compound_pct": 100 * nav, "max_drawdown_pct": drawdown(ret),
        "mean_trade_pct": 100 * selected.net.mean() if len(selected) else np.nan,
        "strong_hit_pct": 100 * selected.strong.mean() if len(selected) else np.nan,
        "critical_count": int(selected.net.lt(-.02).sum()),
        "blocked_critical": int(blocked.net.lt(-.02).sum()),
        "blocked_profitable": int(blocked.net.gt(0).sum()),
        "positive_months": int(by_month.gt(0).sum()),
        "negative_months": int(by_month.lt(0).sum()),
        "without_best_month_pct": 100 * no_best,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = pd.read_csv(SRC, dtype={"symbol6": str}, float_precision="round_trip")
    base = base.sort_values("signal_date").reset_index(drop=True)
    assert len(base) == 284 and base.signal_date.is_unique
    base["executed"] = base.baseline_trade & base.signal_price.gt(0) & base.exit_0940.gt(0)
    assert base.baseline_trade.sum() == base.executed.sum() == 219
    base["net"] = np.where(base.executed,
                           base.exit_0940 * .999 / (base.signal_price * 1.001) - 1,
                           np.nan)
    # CO17 A is the same B1R rolling model, and the shared original trades must match.
    a = pd.read_csv(CO17, dtype={"stock": str})
    a = a[a.variant.str.startswith("A") & a.day.le("2026-09-30")]
    overlap = base[base.signal_date.isin(a.day)].merge(a, left_on="signal_date", right_on="day",
                                                        validate="one_to_one")
    assert len(overlap) == len(a)
    assert (overlap.executed_x == overlap.executed_y).all()
    overlap_trades = overlap[overlap.executed_x]
    assert (overlap_trades.symbol6 == overlap_trades.stock).all()
    np.testing.assert_allclose(overlap_trades.signal_price_x, overlap_trades.signal_price_y, rtol=0, atol=0)
    np.testing.assert_allclose(overlap_trades.exit_0940, overlap_trades.sell_price, rtol=0, atol=0)
    np.testing.assert_allclose(overlap_trades.net_x, overlap_trades.return_net, rtol=0, atol=1e-12)
    # Merge suffixes in pandas depend on the CO17 schema; avoid any outcome in feature construction.
    factor, fund_paths = features(base)
    factor = gate_columns(factor)
    assert factor["executed"].sum() == 219
    factor.to_csv(OUT / "oof_factor_panel.csv.gz", index=False, float_format="%.17g")

    summaries, months, associations, coverage = [], [], [], []
    for name, (start, end) in PERIODS.items():
        scope = factor[factor.signal_date.between(start, end)].copy()
        baseline = evaluate(scope, "all")
        for gate in ["all", *GATES]:
            met = evaluate(scope, gate)
            met.update(period=name, gate=gate,
                       delta_vs_all_pp=met["net_compound_pct"] - baseline["net_compound_pct"])
            summaries.append(met)
            for month, g in scope.groupby("month"):
                mask = g.executed & (g[gate] if gate != "all" else True)
                r = g.net.where(mask, 0.)
                months.append(dict(period=name, gate=gate, month=month, trades=int(mask.sum()),
                                   net_compound_pct=100 * (np.prod(1 + r) - 1),
                                   critical_count=int(g.loc[mask, "net"].lt(-.02).sum())))
        traded = scope[scope.executed]
        for col in FACTORS:
            available = traded[[col, "net", "strong"]].dropna()
            coverage.append(dict(period=name, factor=col, trades=len(traded),
                                 available=len(available)))
            if len(available) >= 10 and available[col].nunique() > 1:
                with np.errstate(invalid="ignore"):
                    corr, p = spearmanr(available[col], available.net)
                associations.append(dict(period=name, factor=col, n=len(available),
                                         spearman_net=float(corr), p_unadjusted=float(p),
                                         critical_pct=100 * available.net.lt(-.02).mean(),
                                         strong_hit_pct=100 * available.strong.mean()))
    summary = pd.DataFrame(summaries)
    monthly = pd.DataFrame(months)
    summary.to_csv(OUT / "summary.csv", index=False)
    monthly.to_csv(OUT / "monthly.csv", index=False)
    pd.DataFrame(associations).to_csv(OUT / "associations.csv", index=False)
    pd.DataFrame(coverage).to_csv(OUT / "coverage.csv", index=False)

    disc = summary[summary.period.eq("discovery_2025")].set_index("gate")
    all_trades = int(disc.loc["all", "trades"])
    candidates = []
    for gate in GATES:
        row = disc.loc[gate]
        dm = monthly[(monthly.period.eq("discovery_2025")) & monthly.gate.eq(gate)]
        bm = monthly[(monthly.period.eq("discovery_2025")) & monthly.gate.eq("all")]
        joined = dm.set_index("month").net_compound_pct - bm.set_index("month").net_compound_pct
        qualifies = (row.trades >= 20 and .2 <= row.trades / all_trades <= .8
                     and row.delta_vs_all_pp > 0 and int(joined.gt(0).sum()) >= 4)
        candidates.append(dict(gate=gate, discovery_trades=int(row.trades),
                               discovery_delta_pp=float(row.delta_vs_all_pp),
                               improved_months=int(joined.gt(0).sum()), qualifies=bool(qualifies)))
    candidates = pd.DataFrame(candidates)
    candidates.to_csv(OUT / "discovery_screen.csv", index=False)
    qualified = candidates[candidates.qualifies].sort_values("discovery_delta_pp", ascending=False)
    chosen = qualified.gate.iloc[0] if len(qualified) else None
    manifest = {
        "experiment": "CO20_20261026_B1R_extended_factor_screen",
        "signal_days": len(factor), "executed_trades": int(factor.executed.sum()),
        "first": factor.signal_date.min(), "last": factor.signal_date.max(),
        "co17_overlap_days": len(overlap), "co17_overlap_trades": len(overlap_trades),
        "chosen_gate_from_discovery": chosen, "fund_vintage_verified": False,
        "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in
                          [SRC, CO17, B1R / "older_adjusted.csv.gz", B1R / "daily.csv.gz", *fund_paths]},
        "output_sha256": {p.name: sha(p) for p in OUT.glob("*.csv*")
                          if not p.name.startswith("._")},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "source_sha256"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
