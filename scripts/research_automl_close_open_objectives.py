"""Isolated close-to-open research: lagged paths, rolling consensus and utility.

All design choices are recorded in DESIGN.md before the first experiment.
This script never changes a production model or another strategy's files.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import platform
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.linear_model import Ridge
from sklearn.preprocessing import SplineTransformer, StandardScaler
from threadpoolctl import threadpool_limits

from scripts.analyze_automl_cost_robustness import net_return
from scripts.research_automl_temporal_stability import (
    FEATURES, SOURCES, benchmark, load_cohorts,
)

ROOT = Path("docs/stock_automl_runs/20261013_close_open_objectives")
DATA = Path("outputs/stock_automl/close_open_objectives_r1")
DAILY = Path("outputs/stock_automl/limit_rebound_2y_r1/price_months")
PATH_FEATURES = (
    "open_gap", "from_open", "lag_return", "lag_upper_shadow",
    "lag_close_position", "prior5_return", "prior5_volatility",
    "prior5_range", "prior5_contract_fraction", "relative_turnover",
    "distance_ma5", "ma5_over_ma20",
)
CONTEXT_FEATURES = ("lag_market_breadth", "breadth_x_signal", "breadth_x_prior5")
EXPANDED = tuple(FEATURES) + PATH_FEATURES + CONTEXT_FEATURES
BINARY = set(FEATURES[4:])
LOG = {"volume_ratio", "turnover_so_far_pct", "float_mv_100m_cny", "relative_turnover"}
META = ("current_return", "current_utility", "mean_return", "std_return",
        "min_return", "rank_return", "top5_fraction", "lag_market_breadth",
        "float_mv_100m_cny", "turnover_so_far_pct")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utility(gross: np.ndarray) -> np.ndarray:
    net = net_return(pd.Series(gross), 50_000, .00025).to_numpy()
    return np.minimum(net, .05) - np.maximum(-net - .02, 0)


def build_paths(raw: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """T open is available at 14:40; T OHLC/flow closes never enter features."""
    d = raw.copy()
    d["symbol6"] = d.stk_code.str[:6]
    d = d[d.symbol6.str.startswith(("0", "3", "6", "9"))].copy()
    d = d.sort_values(["symbol6", "trade_date"]).reset_index(drop=True)
    if d.duplicated(["symbol6", "trade_date"]).any():
        raise ValueError("Duplicate daily source keys")
    dates = sorted(d.trade_date.unique())
    d["day_index"] = d.trade_date.map(dict(zip(dates, range(len(dates)))))
    d["daily_return"] = d.close / d.preclose - 1
    d["log_return"] = np.log1p(d.daily_return)
    d["daily_range"] = (d.high-d.low) / d.preclose
    d["upper_shadow"] = (d.high-d[["open", "close"]].max(axis=1)) / d.preclose
    d["close_position"] = (d.close-d.low) / (d.high-d.low).replace(0, np.nan)
    d.loc[d.high.eq(d.low) & d.high.notna(), "close_position"] = .5
    g = d.groupby("symbol6", sort=False)
    d["contracts"] = d.volume.lt(g.volume.shift(1)).astype(float)
    for target, source in (("lag_return", "daily_return"), ("lag_upper_shadow", "upper_shadow"),
                           ("lag_close_position", "close_position")):
        d[target] = g[source].shift(1)
    d["prior5_return"] = np.expm1(g.log_return.transform(lambda s: s.shift(1).rolling(5).sum()))
    d["prior5_volatility"] = g.daily_return.transform(lambda s: s.shift(1).rolling(5).std(ddof=0))
    d["prior5_range"] = g.daily_range.transform(lambda s: s.shift(1).rolling(5).mean())
    d["prior5_contract_fraction"] = g.contracts.transform(lambda s: s.shift(1).rolling(5).mean())
    d["prior5_turnover"] = g.turn_ratio.transform(lambda s: s.shift(1).rolling(5).mean())
    consecutive5 = d.day_index.sub(g.day_index.shift(5)).eq(5)
    lagcols = ["lag_return", "lag_upper_shadow", "lag_close_position", "prior5_return",
               "prior5_volatility", "prior5_range", "prior5_contract_fraction", "prior5_turnover"]
    d.loc[~consecutive5, lagcols] = np.nan
    # Contraction of five preceding days needs a sixth preceding observation.
    d.loc[~d.day_index.sub(g.day_index.shift(6)).eq(6), "prior5_contract_fraction"] = np.nan
    market = d.groupby("trade_date").daily_return.apply(lambda s: s.dropna().gt(0).mean())
    d["lag_market_breadth"] = d.trade_date.map(market.shift(1))
    d["open_gap"] = d.open / d.preclose - 1
    d = d.rename(columns={"trade_date": "signal_date", "open": "today_open"})
    joined = candidates.merge(d[["signal_date", "symbol6", "today_open", "open_gap",
                                "lag_market_breadth"] + lagcols],
                              on=["signal_date", "symbol6"], how="left", validate="many_to_one")
    joined["from_open"] = joined.signal_price / joined.today_open - 1
    joined["relative_turnover"] = joined.turnover_so_far_pct / joined.prior5_turnover.replace(0, np.nan)
    joined["distance_ma5"] = joined.signal_price / joined.ma5 - 1
    joined["ma5_over_ma20"] = joined.ma5 / joined.ma20 - 1
    joined["breadth_x_signal"] = (joined.lag_market_breadth-.5) * joined.signal_return
    joined["breadth_x_prior5"] = (joined.lag_market_breadth-.5) * joined.prior5_return
    joined["actual"] = joined.close_40 / joined.signal_price - 1
    return joined


class Transform:
    """Small fitted training-window transform, reusable by older model versions."""

    def __init__(self, columns: tuple[str, ...], smooth: bool = False):
        self.columns, self.smooth = columns, smooth
        self.cont = [i for i, name in enumerate(columns) if name not in BINARY]
        self.binary = [i for i, name in enumerate(columns) if name in BINARY]

    def raw(self, frame: pd.DataFrame) -> np.ndarray:
        a = frame[list(self.columns)].to_numpy(float).copy()
        a[~np.isfinite(a)] = np.nan
        for j, name in enumerate(self.columns):
            if name in LOG:
                a[:, j] = np.log1p(np.maximum(a[:, j], 0))
        return a

    def fit_transform(self, frame: pd.DataFrame) -> np.ndarray:
        a = self.raw(frame)
        self.medians = pd.DataFrame(a).median().fillna(0).to_numpy()
        a = np.where(np.isnan(a), self.medians, a)
        self.low, self.high = np.quantile(a[:, self.cont], [.01, .99], axis=0)
        a[:, self.cont] = np.clip(a[:, self.cont], self.low, self.high)
        self.mean, self.std = a[:, self.cont].mean(axis=0), a[:, self.cont].std(axis=0)
        a[:, self.cont] = (a[:, self.cont]-self.mean)/np.maximum(self.std, 1e-10)
        if not self.smooth:
            return a
        self.spline = SplineTransformer(n_knots=4, degree=2, knots="uniform",
                                       extrapolation="constant", include_bias=False)
        z = self.spline.fit_transform(a[:, self.cont])
        self.scaler = StandardScaler()
        z = self.scaler.fit_transform(z)
        return np.column_stack((z, a[:, self.binary]))

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        a = self.raw(frame)
        a = np.where(np.isnan(a), self.medians, a)
        a[:, self.cont] = np.clip(a[:, self.cont], self.low, self.high)
        a[:, self.cont] = (a[:, self.cont]-self.mean)/np.maximum(self.std, 1e-10)
        if not self.smooth:
            return a
        z = self.scaler.transform(self.spline.transform(a[:, self.cont]))
        return np.column_stack((z, a[:, self.binary]))


def fit(frame: pd.DataFrame, columns: tuple[str, ...], smooth: bool, joint: bool = False):
    transform = Transform(columns, smooth)
    x = transform.fit_transform(frame)
    y = np.clip(frame.actual.to_numpy(float), -.10, .10)
    if joint:
        y = np.column_stack((y, utility(frame.actual.to_numpy(float))))
    model = Ridge(alpha=100.0, solver="cholesky").fit(x, y)
    return transform, model


def predict(fitted, test: pd.DataFrame) -> np.ndarray:
    transform, model = fitted
    return model.predict(transform.transform(test))


def history_for(rows: pd.DataFrame, dates: list[str], cutoff: int) -> pd.DataFrame:
    train = rows[rows.signal_date.isin(dates[cutoff-20:cutoff]) & rows.actual.notna()].copy()
    if train.signal_date.nunique() != 20 or not train.next_date.le(dates[cutoff]).all():
        raise ValueError("Missing training dates or immature next-morning labels")
    return train


def record(period: str, day: str, test: pd.DataFrame, name: str,
           scores: np.ndarray, gate: bool = False) -> dict:
    k = np.lexsort((test.symbol6.to_numpy(), -scores))[0]
    row = test.iloc[k]
    traded = not gate or scores[k] > 0
    return {"period": period, "signal_date": day, "model": name,
            "symbol6": row.symbol6 if traded else "", "traded": traded,
            "return": row.actual if traded else 0., "score": float(scores[k]),
            "top1_same_after_2pct_delete": np.nan}


def meta_features(test: pd.DataFrame, versions: np.ndarray) -> pd.DataFrame:
    ret = versions[:, :, 0]
    frame = test[["signal_date", "next_date", "symbol6", "actual",
                  "lag_market_breadth", "float_mv_100m_cny", "turnover_so_far_pct"]].copy()
    frame["current_return"], frame["current_utility"] = versions[:, -1, 0], versions[:, -1, 1]
    frame["mean_return"], frame["std_return"] = ret.mean(axis=1), ret.std(axis=1)
    frame["min_return"] = ret.min(axis=1)
    frame["rank_return"] = pd.Series(ret[:, -1]).rank(pct=True).to_numpy()
    count = np.zeros(len(test))
    for j in range(ret.shape[1]):
        top = np.lexsort((test.symbol6.to_numpy(), -ret[:, j]))[:5]
        count[top] += 1
    frame["top5_fraction"] = count / ret.shape[1]
    return frame


def meta_history(previous: list[pd.DataFrame], day: str) -> pd.DataFrame | None:
    if len(previous) < 20:
        return None
    data = pd.concat(previous[-40:], ignore_index=True)
    data = data[data.actual.notna()].copy()
    if not data.signal_date.lt(day).all() or not data.next_date.le(day).all():
        raise ValueError("Second-stage history contains current/future/immature evidence")
    return data


def summarize(picks: pd.DataFrame) -> pd.DataFrame:
    results = []
    combined = picks.copy()
    combined["period"] = "combined"
    for (period, name), d in pd.concat([picks, combined]).groupby(["period", "model"]):
        valid = d[d.net.notna()]
        trades = valid[valid.traded]
        m = valid.groupby("month").net.mean()
        if not len(m):
            continue
        best = m.idxmax()
        drop = trades.groupby("month").net.idxmax() if len(trades) else []
        without = valid.drop(drop)
        n_tail = max(1, int(np.ceil(len(trades)*.1)))
        results.append({"period": period, "model": name, "days": len(d),
                        "trades": int(d.traded.sum()), "unknown": int(d.net.isna().sum()),
                        "coverage_pct": d.traded.mean()*100,
                        "net_daily_pct": valid.net.mean()*100, "net_trade_pct": trades.net.mean()*100,
                        "gross_trade_pct": trades["return"].mean()*100,
                        "win_rate_pct": trades.net.gt(0).mean()*100,
                        "loss_below_2pct_pct": trades.net.lt(-.02).mean()*100,
                        "loss_below_5pct_pct": trades.net.lt(-.05).mean()*100,
                        "worst10pct_trade_mean_pct": trades.net.nsmallest(n_tail).mean()*100,
                        "avg_win_pct": trades.loc[trades.net.gt(0), "net"].mean()*100,
                        "avg_loss_pct": trades.loc[trades.net.le(0), "net"].mean()*100,
                        "positive_months": int(m.gt(0).sum()), "months": len(m),
                        "worst_month_pct": m.min()*100, "median_month_pct": m.median()*100,
                        "best_month": best,
                        "ex_best_month_pct": valid.loc[valid.month.ne(best), "net"].mean()*100,
                        "ex_month_top_trade_pct": without.net.mean()*100,
                        "winsor5_net_pct": valid.net.clip(-.05, .05).mean()*100,
                        "top1_same_after_2pct_delete": d.top1_same_after_2pct_delete.mean()})
    return pd.DataFrame(results)


def run() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    cache = DATA / "candidate_paths.csv.gz"
    manifest_path = DATA / "input_manifest.json"
    if cache.exists():
        manifest = json.loads(manifest_path.read_text())
        if sha(cache) != manifest["feature_panel_sha256"]:
            raise ValueError("Frozen feature panel hash changed")
        rows = pd.read_csv(cache, dtype={"symbol6": str, "period": str})
        print("Reused frozen feature panel; all fits below are new", flush=True)
    else:
        cohorts = load_cohorts()
        candidates = pd.concat([df.assign(period=p) for p, df in cohorts.items()], ignore_index=True)
        paths = sorted(p for p in DAILY.glob("price_*.csv.gz")
                       if "2024-12" <= p.name[6:13] <= "2026-09")
        raw = pd.concat([pd.read_csv(p, dtype={"stk_code": str},
                                    usecols=["trade_date", "stk_code", "preclose", "open", "high", "low",
                                             "close", "volume", "turn_ratio"]) for p in paths], ignore_index=True)
        rows = build_paths(raw, candidates)
        keep = ["period", "signal_date", "next_date", "symbol6", "actual"] + list(EXPANDED)
        rows = rows[keep]
        rows.to_csv(cache, index=False)
        manifest = {"daily": [{"path": str(p), "sha256": sha(p)} for p in paths],
                    "cohorts": {p: {"path": str(path), "sha256": sha(path)} for p, path in SOURCES.items()},
                    "feature_panel": str(cache), "feature_panel_sha256": sha(cache),
                    "historical_st": "2025/September filtered via current read-only historical status intervals"}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Built new features from cached raw snapshots: {len(rows)} candidates", flush=True)
    rows["kept"] = [int.from_bytes(hashlib.blake2b(f"{d}:{s}".encode(), digest_size=8).digest(), "big")
                    / 2**64 >= .02 for d, s in zip(rows.signal_date, rows.symbol6)]
    rows.groupby("period")[list(EXPANDED)].agg(lambda s: s.isna().mean()).to_csv(ROOT / "feature_missingness.csv")
    baseline = benchmark()
    picks, audits, oof = [], [], []
    for period, group in rows.groupby("period", sort=False):
        dates = sorted(group.signal_date.unique())
        versions, previous_meta = deque(maxlen=5), []
        for i in range(20, len(dates)):
            day = dates[i]
            test = group[group.signal_date.eq(day)].copy()
            if not test.actual.notna().any():
                continue
            train = history_for(group, dates, i)
            current = fit(train, EXPANDED, True, joint=True)
            versions.append((day, current))
            audits.append({"period": period, "signal_date": day, "train_start": dates[i-20],
                           "train_end": dates[i-1], "label_last": train.next_date.max(),
                           "train_n": len(train), "test_n": len(test),
                           "version_count": len(versions)})
            if len(versions) < 5:
                continue
            base = predict(fit(train, tuple(FEATURES), False), test)
            expanded = predict(fit(train, EXPANDED, False), test)
            v = np.stack([predict(f, test) for _, f in versions], axis=1)
            consensus = np.median(v, axis=1) - v.std(axis=1)
            specs = {"ridge7": base, "ridge_path": expanded, "spline_path": v[:, -1, 0],
                     "spline_utility": v[:, -1, 1], "spline_consensus": consensus[:, 0],
                     "utility_consensus": consensus[:, 1], "utility_consensus_gate": consensus[:, 1]}
            reduced = train[train.kept]
            perturbed = {"ridge7": predict(fit(reduced, tuple(FEATURES), False), test),
                         "spline_utility": predict(fit(reduced, EXPANDED, True, joint=True), test)[:, 1]}
            for name, scores in specs.items():
                r = record(period, day, test, name, scores, gate=name.endswith("_gate"))
                if name in perturbed:
                    r["top1_same_after_2pct_delete"] = r["symbol6"] == record(
                        period, day, test, name, perturbed[name])["symbol6"]
                picks.append(r)
            tree = baseline[baseline.year.eq(period) & baseline.signal_date.eq(day)]
            if len(tree) > 1:
                raise ValueError("Duplicate baseline date")
            picks.append({"period": period, "signal_date": day, "model": "tree171", "traded": bool(len(tree)),
                          "symbol6": tree.iloc[0].symbol6 if len(tree) else "",
                          "return": tree.iloc[0]["return"] if len(tree) else 0.})
            mf = meta_features(test, v)
            history = meta_history(previous_meta, day)
            if history is not None:
                transform = Transform(META)
                x = transform.fit_transform(history)
                model = Ridge(alpha=100., solver="cholesky").fit(x, utility(history.actual.to_numpy()))
                scores = model.predict(transform.transform(mf))
                picks.append(record(period, day, test, "meta_utility", scores))
                picks.append(record(period, day, test, "meta_utility_gate", scores, gate=True))
                audits[-1].update(meta_train_start=history.signal_date.min(),
                                  meta_train_end=history.signal_date.max(), meta_train_n=len(history))
            previous_meta.append(mf)
            oof.append(mf.assign(period=period))
            if i % 20 == 0 or i == len(dates)-1:
                print(f"{period} {day}: {i+1}/{len(dates)} dates; fresh rolling fits complete", flush=True)
        pd.DataFrame(picks).to_csv(DATA / "picks_checkpoint.csv", index=False)
    selected = pd.DataFrame(picks)
    selected["month"] = selected.signal_date.str[:7]
    selected["net"] = net_return(selected["return"], 50_000, .00025)
    selected.loc[~selected.traded, "net"] = 0.
    selected.to_csv(ROOT / "picks.csv", index=False)
    pd.DataFrame(audits).to_csv(ROOT / "folds.csv", index=False)
    pd.concat(oof, ignore_index=True).to_csv(DATA / "out_of_fold_predictions.csv.gz", index=False)
    core = selected[~selected.model.str.startswith("meta_")]
    metrics = summarize(core)
    metrics.to_csv(ROOT / "metrics.csv", index=False)
    selected.groupby(["period", "model", "month"]).agg(
        days=("signal_date", "count"), trades=("traded", "sum"), known=("net", "count"),
        net_mean=("net", "mean"), gross_mean=("return", "mean")).to_csv(ROOT / "monthly.csv")
    meta_days = selected.loc[selected.model.eq("meta_utility"), ["period", "signal_date"]]
    common = selected.merge(meta_days, on=["period", "signal_date"], validate="many_to_one")
    summarize(common).to_csv(ROOT / "meta_common_dates_metrics.csv", index=False)
    manifest.update(git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    sklearn=sklearn.__version__, python=platform.python_version(),
                    script_sha256=sha(Path(__file__)), design_sha256=sha(ROOT / "DESIGN.md"),
                    feature_count=len(EXPANDED), features=list(EXPANDED),
                    all_models_fitted_fresh=True, old_tree="saved predictions, unchanged",
                    limitations=["repeatedly studied historical months; not untouched holdout",
                                 "cash=0 on genuine abstention; missing trade outcomes remain NaN",
                                 "no slippage/fill verification; all historical daily snapshots may be revised"])
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(metrics[["period", "model", "days", "trades", "unknown", "net_daily_pct",
                   "positive_months", "months", "worst_month_pct", "ex_month_top_trade_pct"]].to_string(index=False))


if __name__ == "__main__":
    with threadpool_limits(limits=1):
        run()
