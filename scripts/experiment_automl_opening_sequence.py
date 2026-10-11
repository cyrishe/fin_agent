"""Compare a six-bar 1D CNN with flat baselines on already-selected Top1 trades.

All inputs are available by 09:36 on the morning after the selection. Market
context is computed from the local full-market one-minute archives, not from
the selected stock alone. September 2026 lacks that archive and is evaluated
only for configurations that do not require it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from torch import nn

from scripts.benchmark_automl_1440_inference import db_connection, query
from scripts.mk_minute_archive import COMPACT_ROOT, COMPACT_ROOT_2025, day_file


ROOT = Path("docs/stock_automl_runs/20261011_opening_shape_prototype")
CACHE = Path("outputs/stock_automl/opening_sequence_context.csv.gz")
SEP_CACHE = Path("outputs/stock_automl/opening_sequence_context_2026_sep.csv.gz")
SOURCE = Path("outputs/stock_automl/opening_shape_top1_2025_2026.csv.gz")
SEP_SOURCE = Path("outputs/stock_automl/opening_shape_top1_2026_sep.csv.gz")
LABELS = ("持续升高", "冲高回落", "持续走低", "起伏/其他")
BAR_CHANNELS = ("open_ret", "high_ret", "low_ret", "close_ret", "log_volume", "log_amount")
MORNING_SUMMARY = ("first6_move_pct", "first6_range_pct", "g2_minus_g1_pct",
                   "first6_up_bar_count")
PRIOR_1440 = ("signal_return", "volume_ratio", "turnover_so_far_pct", "log_float_mv")
PREV_FULL_DAY = ("prev_open_to_close_pct", "prev_range_pct", "prev_close_position",
                 "prev_log_turnover", "prev_last20_pct")
MARKET_0936 = ("market_0936_mean_return_pct", "market_0936_up_share",
               "market_0931_0936_log_amount_100m")
OPENING_LEVELS = ("opening_gap_pct", "opening_mean_log_volume",
                  "opening_mean_log_amount")


def source_rows() -> pd.DataFrame:
    rows = pd.concat([pd.read_csv(SOURCE, dtype={"symbol6": str}),
                      pd.read_csv(SEP_SOURCE, dtype={"symbol6": str})], ignore_index=True)
    if len(rows) != 294 or rows.duplicated(["signal_date", "symbol6"]).any():
        raise ValueError("Unexpected Top1 sample identity or count")
    return rows.sort_values("signal_date").reset_index(drop=True)


def _market_morning(bars: pd.DataFrame, day: str) -> dict:
    shares = bars.symbol.str.endswith((".SH", ".SZ")) & bars.symbol.str[0].isin(("0", "3", "6"))
    morning = bars.loc[shares & bars.time.str[-8:].between("09:31:00", "09:36:00")]
    at_open = morning[morning.time.str.endswith("09:31:00")][["symbol", "open"]]
    at_last = morning[morning.time.str.endswith("09:36:00")][["symbol", "close"]]
    if at_open.symbol.duplicated().any() or at_last.symbol.duplicated().any():
        raise ValueError(f"Duplicate market opening bars: {day}")
    paired = at_open.merge(at_last, on="symbol", validate="one_to_one")
    if len(paired) < 4000 or paired.open.le(0).any() or morning.turnover.isna().any():
        raise ValueError(f"Incomplete market opening bars: {day}")
    returns = (paired.close / paired.open - 1) * 100
    return {"next_date": day,
            "market_0936_mean_return_pct": float(returns.mean()),
            "market_0936_up_share": float(returns.gt(0).mean()),
            "market_0931_0936_log_amount_100m": float(np.log1p(morning.turnover.sum() / 1e8)),
            "market_0936_stock_count": len(paired)}


def _previous_stock(bars: pd.DataFrame, day: str, symbol6: str) -> dict:
    stock = bars[bars.symbol.str[:6].eq(symbol6)].sort_values("time")
    if len(stock) != 240 or stock.time.iloc[0][-8:] != "09:31:00" or stock.time.iloc[-1][-8:] != "15:00:00":
        raise ValueError(f"Incomplete previous-day stock bars: {day} {symbol6}")
    close_1440 = stock.loc[stock.time.str.endswith("14:40:00"), "close"]
    if len(close_1440) != 1:
        raise ValueError(f"Missing 14:40 stock close: {day} {symbol6}")
    first = float(stock.open.iloc[0])
    last = float(stock.close.iloc[-1])
    low = float(stock.low.min())
    high = float(stock.high.max())
    if first <= 0 or low <= 0 or high < low:
        raise ValueError(f"Invalid previous-day stock prices: {day} {symbol6}")
    return {"signal_date": day, "symbol6": symbol6,
            "prev_open_to_close_pct": (last / first - 1) * 100,
            "prev_range_pct": (high - low) / first * 100,
            "prev_close_position": (last - low) / (high - low) if high > low else .5,
            "prev_log_turnover": float(np.log1p(stock.turnover.sum() / 1e8)),
            "prev_last20_pct": (last / float(close_1440.iloc[0]) - 1) * 100}


def build_context_cache(rows: pd.DataFrame) -> pd.DataFrame:
    archived = rows[rows.source.ne("2026_sep_top1")]
    next_days = set(archived.next_date)
    signal_by_day = {row.signal_date: row.symbol6 for row in archived.itertuples(index=False)}
    all_days = sorted(next_days | set(signal_by_day))
    market_records, previous_records = [], []
    for index, day in enumerate(all_days, start=1):
        root = COMPACT_ROOT_2025 if day.startswith("2025-") else COMPACT_ROOT
        bars = pd.read_csv(day_file(root, day),
                           usecols=["time", "symbol", "open", "high", "low",
                                    "close", "turnover"], dtype={"symbol": str})
        if day in next_days:
            market_records.append(_market_morning(bars, day))
        if day in signal_by_day:
            previous_records.append(_previous_stock(bars, day, signal_by_day[day]))
        if index % 25 == 0 or index == len(all_days):
            print(f"context days {index}/{len(all_days)}", flush=True)
    context = archived[["signal_date", "next_date", "symbol6"]].merge(
        pd.DataFrame(market_records), on="next_date", validate="one_to_one").merge(
        pd.DataFrame(previous_records), on=["signal_date", "symbol6"], validate="one_to_one")
    if len(context) != len(archived) or context[list(PREV_FULL_DAY + MARKET_0936)].isna().any().any():
        raise ValueError("Missing opening-sequence context")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    context.to_csv(CACHE, index=False, compression="gzip", float_format="%.9f")
    return context


def _db_minute_bars(conn, day: str, symbol6: str | None = None) -> pd.DataFrame:
    start = f"{day} 09:31:00"
    end = f"{day} 15:00:00" if symbol6 else f"{day} 09:36:00"
    rows = query(conn, """SELECT stk_code, bar_end_time, open_price, high_price,
        low_price, latest_price, amount, is_finalized, is_fallback, source_snapshot_time
        FROM aiia_stock_realtime_minute_snapshot_full
        WHERE trade_date=%s AND kline_type='1m' AND period_minutes=1
          AND bar_end_time BETWEEN %s AND %s
          AND (%s IS NULL OR stk_code=%s)
        ORDER BY stk_code, bar_end_time""", (day, start, end, symbol6, symbol6))
    if rows.empty:
        raise ValueError(f"Missing database minute bars: {day} {symbol6 or 'market'}")
    if not (rows.is_finalized.eq(1).all() and rows.is_fallback.eq(0).all() and
            pd.to_datetime(rows.source_snapshot_time).eq(
                pd.to_datetime(rows.bar_end_time)).all()):
        raise ValueError(f"Non-final database minute bars: {day} {symbol6 or 'market'}")
    codes = rows.stk_code.astype(str).str.zfill(6)
    suffix = np.where(codes.str.startswith("6"), ".SH", ".SZ")
    bars = pd.DataFrame({
        "time": pd.to_datetime(rows.bar_end_time).dt.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": codes + suffix,
        "open": pd.to_numeric(rows.open_price, errors="raise"),
        "high": pd.to_numeric(rows.high_price, errors="raise"),
        "low": pd.to_numeric(rows.low_price, errors="raise"),
        "close": pd.to_numeric(rows.latest_price, errors="raise"),
        "turnover": pd.to_numeric(rows.amount, errors="raise"),
    })
    return bars


def build_sep_context_cache(rows: pd.DataFrame) -> pd.DataFrame:
    sep = rows[rows.source.eq("2026_sep_top1")]
    market_records, previous_records = [], []
    with db_connection(Path(".env")) as conn:
        for index, rec in enumerate(sep.itertuples(index=False), start=1):
            market_records.append(_market_morning(
                _db_minute_bars(conn, rec.next_date), rec.next_date))
            previous_records.append(_previous_stock(
                _db_minute_bars(conn, rec.signal_date, rec.symbol6),
                rec.signal_date, rec.symbol6))
            if index % 5 == 0:
                print(f"September database context {index}/{len(sep)}", flush=True)
    context = sep[["signal_date", "next_date", "symbol6"]].merge(
        pd.DataFrame(market_records), on="next_date", validate="one_to_one").merge(
        pd.DataFrame(previous_records), on=["signal_date", "symbol6"], validate="one_to_one")
    if len(context) != len(sep) or context[list(PREV_FULL_DAY + MARKET_0936)].isna().any().any():
        raise ValueError("Missing September opening-sequence context")
    SEP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    context.to_csv(SEP_CACHE, index=False, compression="gzip", float_format="%.9f")
    return context


def attach_context(rows: pd.DataFrame) -> pd.DataFrame:
    context = pd.read_csv(CACHE, dtype={"symbol6": str}) if CACHE.exists() else build_context_cache(rows)
    sep_context = (pd.read_csv(SEP_CACHE, dtype={"symbol6": str}) if SEP_CACHE.exists()
                   else build_sep_context_cache(rows))
    context = pd.concat([context, sep_context], ignore_index=True)
    data = rows.merge(context, on=["signal_date", "next_date", "symbol6"], how="left",
                      validate="one_to_one")
    if data[list(PREV_FULL_DAY + MARKET_0936)].isna().any().any():
        raise ValueError("Top1 rows lack previous-stock or morning-market context")
    return data


def sequence_array(rows: pd.DataFrame) -> np.ndarray:
    return np.stack([
        rows[[f"{channel}_{i}" for channel in BAR_CHANNELS]].to_numpy(dtype=np.float32)
        for i in range(1, 7)
    ], axis=1)


def relative_sequence_array(raw: np.ndarray) -> np.ndarray:
    """Keep six-bar order while centering each stock's price and activity path."""
    result = raw.copy()
    result[:, :, :4] -= raw[:, :1, :1]
    result[:, :, 4:] -= raw[:, :, 4:].mean(axis=1, keepdims=True)
    return result


def normalize(train_seq: np.ndarray, seq: np.ndarray,
              train_aux: np.ndarray, aux: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    seq_mean = train_seq.mean(axis=(0, 1), keepdims=True)
    seq_std = train_seq.std(axis=(0, 1), keepdims=True).clip(min=1e-6)
    seq_scaled = (seq - seq_mean) / seq_std
    if train_aux.shape[1]:
        aux_mean = train_aux.mean(axis=0, keepdims=True)
        aux_std = train_aux.std(axis=0, keepdims=True).clip(min=1e-6)
        aux_scaled = (aux - aux_mean) / aux_std
    else:
        aux_scaled = aux
    return seq_scaled.astype(np.float32), aux_scaled.astype(np.float32)


class TinyOpeningCNN(nn.Module):
    def __init__(self, auxiliary_count: int):
        super().__init__()
        self.sequence = nn.Sequential(nn.Conv1d(6, 12, kernel_size=2), nn.ReLU(),
                                      nn.Conv1d(12, 12, kernel_size=2), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(12 * 4 + auxiliary_count, 24), nn.ReLU(),
                                  nn.Dropout(.35), nn.Linear(24, 4))

    def forward(self, sequence: torch.Tensor, auxiliary: torch.Tensor) -> torch.Tensor:
        encoded = self.sequence(sequence.transpose(1, 2)).flatten(1)
        return self.head(torch.cat((encoded, auxiliary), dim=1))


def train_cnn(train_seq: np.ndarray, train_aux: np.ndarray, train_y: np.ndarray,
              val_seq: np.ndarray, val_aux: np.ndarray, val_y: np.ndarray,
              seed: int) -> tuple[TinyOpeningCNN, int]:
    torch.manual_seed(seed)
    model = TinyOpeningCNN(train_aux.shape[1])
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.03)
    loss_fn = nn.CrossEntropyLoss()
    x = torch.from_numpy(train_seq)
    a = torch.from_numpy(train_aux)
    y = torch.from_numpy(train_y)
    vx = torch.from_numpy(val_seq)
    va = torch.from_numpy(val_aux)
    vy = torch.from_numpy(val_y)
    rng = np.random.default_rng(seed)
    best_loss, best_epoch, best_state = np.inf, 0, None
    for epoch in range(1, 101):
        model.train()
        for batch in np.array_split(rng.permutation(len(y)), 4):
            optimizer.zero_grad()
            loss = loss_fn(model(x[batch], a[batch]), y[batch])
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(model(vx, va), vy))
        if val_loss < best_loss - 1e-4:
            best_loss, best_epoch = val_loss, epoch
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
        if epoch - best_epoch >= 15:
            break
    assert best_state is not None
    model.load_state_dict(best_state)
    model.eval()
    return model, best_epoch


def score(rows: pd.DataFrame, predicted: np.ndarray) -> dict:
    actual = rows.shape_label.to_numpy()
    down = predicted == "持续走低"
    true_down = actual == "持续走低"
    correct_down = int((down & true_down).sum())
    return {"samples": len(rows),
            "accuracy": round(float(accuracy_score(actual, predicted)), 6),
            "balanced_accuracy": round(float(balanced_accuracy_score(actual, predicted)), 6),
            "predicted_down": int(down.sum()), "correct_down": correct_down,
            "down_precision": round(correct_down / int(down.sum()), 6) if down.sum() else None,
            "down_recall": round(correct_down / int(true_down.sum()), 6)}


def main() -> None:
    torch.set_num_threads(1)
    rows = attach_context(source_rows())
    rows["opening_gap_pct"] = rows.open_ret_1
    rows["opening_mean_log_volume"] = rows[[f"log_volume_{i}" for i in range(1, 7)]].mean(axis=1)
    rows["opening_mean_log_amount"] = rows[[f"log_amount_{i}" for i in range(1, 7)]].mean(axis=1)
    train = rows[(rows.source.eq("2025_top1")) & rows.signal_date.between("2025-03-05", "2025-08-29")]
    val = rows[(rows.source.eq("2025_top1")) & rows.signal_date.between("2025-09-01", "2025-10-31")]
    test = rows[rows.source.eq("2026_top1")]
    sep = rows[rows.source.eq("2026_sep_top1")]
    if tuple(map(len, (train, val, test, sep))) != (120, 39, 115, 20):
        raise ValueError("Unexpected chronological sample split")
    all_sequence = sequence_array(rows)
    relative_sequence = relative_sequence_array(all_sequence)
    label_to_index = {label: i for i, label in enumerate(LABELS)}
    train_y = np.array([label_to_index[label] for label in train.shape_label], dtype=np.int64)
    val_y = np.array([label_to_index[label] for label in val.shape_label], dtype=np.int64)
    indexes = {"train": train.index.to_numpy(), "validation": val.index.to_numpy(),
               "2026_feb_jul": test.index.to_numpy(), "2026_sep": sep.index.to_numpy()}
    groups = {"train": train, "validation": val, "2026_feb_jul": test, "2026_sep": sep}
    configurations = {
        "cnn_sequence": ((), False),
        "cnn_plus_1440": (PRIOR_1440, False),
        "cnn_plus_yesterday": (PRIOR_1440 + PREV_FULL_DAY, False),
        "cnn_plus_yesterday_market": (PRIOR_1440 + PREV_FULL_DAY + MARKET_0936, False),
        "cnn_relative_sequence": (OPENING_LEVELS, True),
        "cnn_relative_yesterday": (OPENING_LEVELS + PRIOR_1440 + PREV_FULL_DAY, True),
        "cnn_relative_market": (OPENING_LEVELS + MARKET_0936, True),
        "cnn_relative_yesterday_market": (
            OPENING_LEVELS + PRIOR_1440 + PREV_FULL_DAY + MARKET_0936, True),
    }
    result = {"split_counts": {key: len(group) for key, group in groups.items()},
              "architecture": "1D Conv(6->12,k2), Conv(12->12,k2), flattened, dense24, dropout0.35; AdamW, up to 100 epochs; 2025 validation early stop",
              "market_definition": "Shanghai/Shenzhen A shares with 09:31 open and 09:36 close; equal-weight change, rising share, first-six-minute amount",
              "seeds": [0, 1, 2, 3, 4], "configurations": {}, "linear_comparisons": {}}
    predictions = []
    for config, (aux_columns, relative) in configurations.items():
        available = ("train", "validation", "2026_feb_jul", "2026_sep")
        source_sequence = relative_sequence if relative else all_sequence
        train_aux = train[list(aux_columns)].to_numpy(dtype=np.float32)
        config_runs = []
        for seed in result["seeds"]:
            train_seq_scaled, train_aux_scaled = normalize(
                source_sequence[indexes["train"]], source_sequence[indexes["train"]],
                train_aux, train_aux)
            val_seq_scaled, val_aux_scaled = normalize(
                source_sequence[indexes["train"]], source_sequence[indexes["validation"]],
                train_aux, val[list(aux_columns)].to_numpy(dtype=np.float32))
            model, epoch = train_cnn(train_seq_scaled, train_aux_scaled, train_y,
                                     val_seq_scaled, val_aux_scaled, val_y, seed)
            run = {"seed": seed, "best_epoch": epoch, "splits": {}}
            for split in available:
                group = groups[split]
                seq_scaled, aux_scaled = normalize(
                    source_sequence[indexes["train"]], source_sequence[indexes[split]],
                    train_aux, group[list(aux_columns)].to_numpy(dtype=np.float32))
                with torch.no_grad():
                    logits = model(torch.from_numpy(seq_scaled), torch.from_numpy(aux_scaled))
                    pred_indices = logits.argmax(dim=1).numpy()
                predicted = np.array(LABELS, dtype=object)[pred_indices]
                run["splits"][split] = score(group, predicted)
                if seed == 0:
                    for rec, label in zip(group.itertuples(index=False), predicted):
                        predictions.append({"configuration": config, "split": split,
                                            "signal_date": rec.signal_date,
                                            "symbol6": rec.symbol6,
                                            "actual_shape": rec.shape_label,
                                            "predicted_shape": label})
            config_runs.append(run)
        result["configurations"][config] = config_runs
        print(f"finished {config}", flush=True)
    flat40 = [f"{channel}_{i}" for i in range(1, 7) for channel in BAR_CHANNELS] + list(MORNING_SUMMARY)
    for config, features in (("linear_original40", flat40),
                             ("linear_plus_yesterday", flat40 + list(PRIOR_1440 + PREV_FULL_DAY)),
                             ("linear_plus_market", flat40 + list(MARKET_0936)),
                             ("linear_all_context", flat40 + list(PRIOR_1440 + PREV_FULL_DAY + MARKET_0936))):
        model = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000))
        model.fit(train[features], train.shape_label)
        available = ("train", "validation", "2026_feb_jul", "2026_sep")
        result["linear_comparisons"][config] = {
            split: score(groups[split], model.predict(groups[split][features])) for split in available}
    relative_flat = relative_sequence.reshape(len(rows), -1)
    relative_aux = rows[list(OPENING_LEVELS + PRIOR_1440 + PREV_FULL_DAY + MARKET_0936)].to_numpy(dtype=np.float32)
    relative_input = np.concatenate((relative_flat, relative_aux), axis=1)
    relative_linear = make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=1000))
    relative_linear.fit(relative_input[indexes["train"]], train.shape_label)
    result["linear_comparisons"]["linear_relative_all_context"] = {
        split: score(groups[split], relative_linear.predict(relative_input[indexes[split]]))
        for split in groups}
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "sequence_model_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    pd.DataFrame(predictions).to_csv(ROOT / "sequence_seed0_predictions.csv", index=False)
    print(json.dumps({"linear": result["linear_comparisons"],
                      "cnn": {name: [run["splits"].get("2026_feb_jul") for run in runs]
                              for name, runs in result["configurations"].items()}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
