"""Reprice a freshly trained Jan-Jul B1 Top1 with the stock amount abstention rule."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.append_automl_stock_amount_state import archive_amounts, required_bars
from scripts.mk_minute_archive import COMPACT_ROOT, day_files, trading_day


DEFAULT_RUN = Path("docs/stock_automl_runs/20261012_b1_rerun_feb_jul")


def run(output: Path, archive: Path = COMPACT_ROOT) -> dict:
    top1_path = output / "top1_verified.csv"
    picks = pd.read_csv(top1_path, dtype={"symbol6": str, "signal_date": str,
                                         "next_date": str})
    folds = json.loads((output / "folds.json").read_text())
    calendar = [trading_day(path) for path in day_files([archive])]
    if len(calendar) != 139 or len(folds) != 118 or picks.signal_date.duplicated().any():
        raise ValueError("Expected 139 archive dates, 118 folds and unique Top1 days")
    top1_sha = hashlib.sha256(top1_path.read_bytes()).hexdigest()
    amount_path = output / "amount_1440.csv.gz"
    amount_manifest = output / "amount_1440_manifest.json"
    expected_manifest = {"top1_sha256": top1_sha, "archive": str(archive.resolve())}
    if amount_path.exists() and amount_manifest.exists() and json.loads(
            amount_manifest.read_text()) == expected_manifest:
        amounts = pd.read_csv(amount_path, dtype={"date": str, "symbol6": str})
    else:
        rows = [{"选股日": r.signal_date, "Top1代码": r.symbol6}
                for r in picks.itertuples(index=False)]
        needed = required_bars(rows, calendar)
        amounts = pd.DataFrame(archive_amounts(needed, root=archive, before=None))
        amounts.to_csv(amount_path, index=False,
                       compression={"method": "gzip", "mtime": 0})
        amount_manifest.write_text(json.dumps(expected_manifest, indent=2) + "\n")
    if amounts.duplicated(["date", "symbol6"]).any():
        raise ValueError("Duplicate as-of stock amount")
    values = {(r.date, r.symbol6): float(r.amount_100m_cny)
              for r in amounts.itertuples(index=False)}
    positions = {day: index for index, day in enumerate(calendar)}
    by_day = picks.set_index("signal_date")
    records = []
    for fold in folds:
        day = fold["test_date"]
        if day not in positions or positions[day] < 20:
            raise ValueError(f"Missing 20 previous market dates: {day}")
        base = {"signal_date": day, "next_date": calendar[positions[day] + 1],
                "training_rows": fold["training_rows"],
                "candidate_count": fold["inference_candidates"]}
        if day not in by_day.index:
            records.append({**base, "symbol6": "", "name": "", "gate": "no_model_pick",
                            "return_0940": 0.0, "p_ge3": np.nan,
                            "actual_class": "", "actual_0940_class": ""})
            continue
        pick = by_day.loc[day]
        if pick.next_date != base["next_date"] or pd.isna(pick.actual_0940_return):
            raise ValueError(f"Missing or mismatched next-morning outcome: {day}")
        previous = [values.get((d, pick.symbol6))
                    for d in calendar[positions[day] - 20:positions[day]]]
        current = values.get((day, pick.symbol6))
        complete = all(v is not None for v in previous)
        median = float(np.median(previous)) if complete else np.nan
        gate = ("trade" if complete and current is not None and current >= median
                else "missing_amount" if not complete or current is None
                else "below_median")
        records.append({**base, "symbol6": pick.symbol6, "name": pick["name"],
                        "gate": gate, "current_amount_100m": current,
                        "prior20_valid_days": sum(v is not None for v in previous),
                        "prior20_median_100m": median,
                        "signal_price": pick.signal_price, "close_40": pick.close_40,
                        "return_0940": float(pick.actual_0940_return),
                        "p_ge3": pick.p_ge3, "actual_class": pick.actual_class,
                        "actual_0940_class": pick.actual_0940_class})
    daily = pd.DataFrame(records)
    daily["trade_return"] = daily.return_0940.where(daily.gate.eq("trade"), 0.0)
    daily["equity_after_exit"] = 1_000_000 * (1 + daily.trade_return).cumprod()
    daily.to_csv(output / "daily_cash_gate.csv", index=False)
    traded = daily[daily.gate.eq("trade")]
    ungated = daily[daily.symbol6.ne("")]
    monthly = []
    for month, group in daily.groupby(daily.next_date.str[:7], sort=True):
        start_equity = float(group.equity_after_exit.iloc[0] / (1 + group.trade_return.iloc[0]))
        end_equity = float(group.equity_after_exit.iloc[-1])
        monthly.append({"exit_month": month, "model_picks": int(group.symbol6.ne("").sum()),
                        "trades": int(group.gate.eq("trade").sum()),
                        "idle_days": int(group.gate.ne("trade").sum()),
                        "monthly_return": end_equity / start_equity - 1,
                        "ending_equity": end_equity})
    pd.DataFrame(monthly).to_csv(output / "monthly_cash_gate.csv", index=False)
    curve = pd.concat([pd.Series([1_000_000.0]), daily.equity_after_exit], ignore_index=True)
    drawdown = curve.div(curve.cummax()).sub(1)
    daily_std = float(daily.trade_return.std(ddof=1))
    summary = {
        "signal_days": len(daily), "selected_days": len(ungated),
        "trades": len(traded), "idle_days": int(daily.gate.ne("trade").sum()),
        "skipped_below_median": int(daily.gate.eq("below_median").sum()),
        "skipped_missing_amount": int(daily.gate.eq("missing_amount").sum()),
        "model_no_pick": int(daily.gate.eq("no_model_pick").sum()),
        "ungated_mean_return": float(ungated.return_0940.mean()),
        "ungated_compound_return": float((1 + ungated.return_0940).prod() - 1),
        "gated_mean_return_per_trade": float(traded.return_0940.mean()),
        "gated_win_rate": float(traded.return_0940.gt(0).mean()),
        "gated_compound_return": float(daily.equity_after_exit.iloc[-1] / 1_000_000 - 1),
        "ending_equity_cny": float(daily.equity_after_exit.iloc[-1]),
        "max_drawdown": float(drawdown.min()),
        "annualized_volatility_252": daily_std * math.sqrt(252),
        "sharpe_rf0_252": (float(daily.trade_return.mean()) / daily_std * math.sqrt(252)
                            if daily_std > 0 else None),
        "actual_0940_classes_traded": traded.actual_0940_class.value_counts().to_dict(),
        "candidate_sha256": json.loads((output / "summary.json").read_text())["data_sha256"],
        "top1_sha256": top1_sha,
    }
    (output / "cash_gate_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--archive", type=Path, default=COMPACT_ROOT)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.archive), ensure_ascii=False, indent=2))
