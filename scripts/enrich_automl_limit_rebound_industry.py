"""Attach as-of AMAC industry breadth and short-horizon returns to cooling rows."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.build_automl_limit_rebound_features import FEATURES, prepare_daily
from scripts.build_automl_limit_rebound_2y import OUT, SIZE_FEATURES


ADDED = ("industry_member_count", "industry_mean_return_1",
         "industry_mean_return_3", "industry_mean_return_5",
         "industry_return_5_rank", "industry_up_fraction",
         "industry_limit_up_count", "industry_limit_up_fraction")
INDUSTRY_FEATURES = {
    "industry_member_count": "当日有有效 AMAC 一级行业记录并有日线的成分数",
    "industry_mean_return_1": "当日行业成分股日收益的等权平均",
    "industry_mean_return_3": "截至决策日连续 3 个市场交易日行业等权日收益复合",
    "industry_mean_return_5": "截至决策日连续 5 个市场交易日行业等权日收益复合",
    "industry_return_5_rank": "当日所有可计算行业的 5 日复合收益百分位排名",
    "industry_up_fraction": "当日行业成分股收盘上涨比例",
    "industry_limit_up_count": "当日行业成分股收盘封板数",
    "industry_limit_up_fraction": "当日行业成分股收盘封板数/成分数",
}


def attach_industry(daily: pd.DataFrame, features: pd.DataFrame,
                    membership: pd.DataFrame) -> pd.DataFrame:
    """Only a membership whose row was created by that decision date can be used."""
    daily = prepare_daily(daily)
    left = daily[["trade_date", "symbol", "ret", "up"]].copy()
    left["trade_date"] = pd.to_datetime(left.trade_date)
    left = left.sort_values(["trade_date", "symbol"]).reset_index(drop=True)
    right = membership.copy()
    right["symbol"] = right.stk_code.astype(str).str[:6]
    for col in ("begin_date", "end_date", "create_time"):
        right[col] = pd.to_datetime(right[col], errors="coerce")
    right = right.sort_values(["begin_date", "symbol"])
    if right.duplicated(["symbol", "begin_date"]).any():
        raise ValueError("Multiple industry memberships start on one stock-date")
    mapped = pd.merge_asof(left, right[["symbol", "begin_date", "end_date",
                                         "create_time", "industry_code"]],
                           by="symbol", left_on="trade_date", right_on="begin_date",
                           direction="backward", allow_exact_matches=True)
    active = (mapped.create_time.le(mapped.trade_date + pd.Timedelta(hours=17)) &
              (mapped.end_date.isna() | mapped.end_date.ge(mapped.trade_date)))
    mapped.loc[~active, "industry_code"] = pd.NA
    grouped = mapped[mapped.industry_code.notna()].groupby(["trade_date", "industry_code"])
    context = grouped.agg(
        industry_member_count=("ret", "size"),
        industry_mean_return_1=("ret", "mean"),
        industry_up_fraction=("ret", lambda s: float(s.gt(0).mean())),
        industry_limit_up_count=("up", "sum"),
    ).reset_index().sort_values(["industry_code", "trade_date"])
    context["industry_limit_up_fraction"] = (context.industry_limit_up_count /
                                               context.industry_member_count)
    # Rolling returns require consecutive market dates; absence never gets treated as 0%.
    calendar = sorted(left.trade_date.unique())
    date_index = {day: i for i, day in enumerate(calendar)}
    context["day_i"] = context.trade_date.map(date_index)
    for window in (3, 5):
        group = context.groupby("industry_code", sort=False)
        contiguous = context.day_i.sub(group.day_i.shift(window - 1)).eq(window - 1)
        product = group.industry_mean_return_1.transform(
            lambda s: (1 + s).rolling(window, min_periods=window).apply(np.prod, raw=True) - 1)
        context[f"industry_mean_return_{window}"] = product.where(contiguous)
    context["industry_return_5_rank"] = context.groupby("trade_date").industry_mean_return_5.rank(pct=True)
    mapped = mapped.merge(context[["trade_date", "industry_code", *ADDED]],
                          on=["trade_date", "industry_code"], how="left", validate="many_to_one")
    attach = mapped[["trade_date", "symbol", *ADDED]].copy()
    attach["trade_date"] = attach.trade_date.dt.strftime("%Y-%m-%d")
    result = features.merge(attach, left_on=["asof_date", "symbol"],
                            right_on=["trade_date", "symbol"], how="left",
                            validate="one_to_one").drop(columns="trade_date")
    return result


def run(root: Path = OUT) -> dict:
    price_paths = sorted((root / "price_months").glob("price_*.csv.gz"))
    if not price_paths:
        raise FileNotFoundError("Run build_automl_limit_rebound_2y first")
    daily = pd.concat((pd.read_csv(path, dtype={"stk_code": str, "trade_date": str})
                       for path in price_paths), ignore_index=True)
    features = pd.read_csv(root / "features.csv.gz", dtype={"symbol": str})
    with db_connection() as conn:
        membership = query(conn, "SELECT stk_code,begin_date,end_date,create_time,"
                           "industry_code FROM kcrp_stock_industry "
                           "WHERE industry_type=%s AND level=%s", ("AMAC", 1))
        conn.rollback()
    root.mkdir(parents=True, exist_ok=True)
    member_path = root / "amac_membership_snapshot.csv.gz"
    membership.to_csv(member_path, index=False, compression="gzip")
    enriched = attach_industry(daily, features, membership)
    path = root / "features_industry.csv.gz"
    enriched.to_csv(path, index=False, compression="gzip")
    (root / "feature_catalog_2y.json").write_text(json.dumps(
        {**FEATURES, "size_proxy": SIZE_FEATURES,
         "historical_industry": INDUSTRY_FEATURES}, ensure_ascii=False, indent=2) + "\n")
    summary = {"source": "kingdomai.kcrp_stock_industry AMAC level 1",
               "membership_snapshot_sha256": hashlib.sha256(member_path.read_bytes()).hexdigest(),
               "decision_rows": len(enriched),
               "industry_coverage": float(enriched.industry_member_count.notna().mean()),
               "created_before_decision_date_required": True,
               "historical_revision_warning": "Current interval records may have later revisions; create_time alone cannot prove vintage equivalence",
               "generated_at_utc": datetime.now(timezone.utc).isoformat()}
    (root / "industry_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    run()
