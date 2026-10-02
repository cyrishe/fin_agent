from __future__ import annotations

import numpy as np
import pandas as pd
from .data import asof_features

TECHNICAL = ("return_1", "return_3", "return_7", "return_20", "volatility_20",
             "range_ratio", "open_gap", "ma_distance", "volume_ratio", "amount_log", "turn_ratio")


def build_panel(daily, events=()):
    required = {"symbol", "date", "open", "high", "low", "close", "adjopen", "adjhigh", "adjlow", "adjclose", "volume", "amount"}
    if not required <= set(daily):
        raise ValueError(f"missing market columns: {sorted(required - set(daily))}")
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily.date).dt.normalize()
    if daily[["symbol", "date"]].duplicated().any():
        raise ValueError("duplicate daily bar")
    for col in required - {"symbol", "date"}:
        daily[col] = pd.to_numeric(daily[col], errors="raise")
    if (daily[["adjopen", "adjhigh", "adjlow", "adjclose"]].dropna() <= 0).any().any():
        raise ValueError("adjusted prices must be positive; raw/adjusted fallback is not allowed")
    calendar = pd.DatetimeIndex(sorted(daily.date.unique()))
    parts = []
    for symbol, group in daily.groupby("symbol"):
        # Reindex before shifting: a suspended stock must not turn 7 market days into 7 observations.
        g = group.set_index("date").reindex(calendar).rename_axis("date").reset_index()
        g["symbol"] = symbol
        close = g.adjclose
        for n in (1, 3, 7, 20):
            g[f"return_{n}"] = close.pct_change(n, fill_method=None)
        g["volatility_20"] = g.return_1.rolling(20, min_periods=20).std()
        g["range_ratio"] = (g.adjhigh - g.adjlow) / close
        g["open_gap"] = g.adjopen / close.shift(1) - 1
        g["ma_distance"] = close / close.rolling(20, min_periods=20).mean() - 1
        g["volume_ratio"] = g.volume / g.volume.rolling(20, min_periods=20).mean()
        g["amount_log"] = np.log1p(g.amount.clip(lower=0))
        g["decision_at"] = g.date + pd.Timedelta(hours=15, minutes=10)
        g["history_ready"] = close.rolling(21, min_periods=21).count().eq(21)
        parts.append(g)
    panel = pd.concat(parts, ignore_index=True)
    panel["turn_ratio"] = panel.get("turn_ratio", np.nan)
    panel["industry"] = panel.get("industry", "unknown").fillna("unknown") if "industry" in panel else "unknown"
    panel = asof_features(panel, events)
    return panel.replace([np.inf, -np.inf], np.nan)


def labeled_panel(panel, horizon):
    out = panel.copy()
    group = out.groupby("symbol", sort=False)
    out["entry_date"] = group.date.shift(-1)
    out["label_end"] = group.date.shift(-(horizon + 1))
    out["forward_return"] = group.adjopen.shift(-(horizon + 1)) / group.adjopen.shift(-1) - 1
    # Require every bar across the holding interval, so unpriced suspensions do not become fills.
    valid = group.adjopen.transform(lambda s: s.notna().rolling(horizon + 1).sum().shift(-(horizon + 1)))
    out = out[out.history_ready & out.forward_return.notna() & valid.eq(horizon + 1) & out.volume.gt(0)]
    return out.reset_index(drop=True)


def feature_columns(panel, feature_set, extra_columns=()):
    cols = list(TECHNICAL)
    if feature_set == "enriched":
        cols += [c for c in ("total_mv", "pe_ttm", "pb_mrq", "industry", "news_count_7d",
                             "minute_volatility", "minute_bars", *extra_columns) if c in panel]
    return list(dict.fromkeys(cols))


def sample_mask(frame, sampler, spec=None):
    mask = _strategy_mask(frame, sampler)
    if spec is not None:
        if spec.industries:
            mask &= frame.industry.isin(spec.industries)
        for field, bound, lower in (("total_mv", spec.min_market_cap, True), ("total_mv", spec.max_market_cap, False), ("amount", spec.min_amount, True)):
            if bound is not None:
                if field not in frame:
                    raise ValueError(f"constraint requires missing feature: {field}")
                mask &= frame[field].ge(bound) if lower else frame[field].le(bound)
    return mask


def _strategy_mask(frame, sampler):
    if sampler == "all":
        return pd.Series(True, index=frame.index)
    if sampler == "momentum":
        return frame.return_7.gt(0) & frame.ma_distance.gt(0)
    if sampler == "reversal":
        return frame.return_3.lt(-.02)
    if sampler == "liquid":
        return frame.volume_ratio.gt(1) & frame.amount_log.gt(np.log1p(1e7))
    if sampler == "low_volatility":
        return frame.volatility_20.lt(.025)
    if sampler == "news":
        if "news_count_7d" not in frame:
            raise ValueError("news sampler requires timestamped news evidence")
        return frame.news_count_7d.gt(0)
    raise ValueError("unsupported sampler")
