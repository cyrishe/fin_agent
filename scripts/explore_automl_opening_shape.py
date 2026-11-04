"""Render auditable 09:31–09:40 OHLCV shape examples from 2025 Top1 picks."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from scripts.mk_minute_archive import COMPACT_ROOT_2025, day_file


TRADES = Path("docs/stock_automl_runs/20261011_mk_2025_mar_aug_backtest/trades.csv")
OUT = Path("docs/stock_automl_runs/20261011_opening_shape_prototype")
GROUPS = (slice(0, 3), slice(3, 6), slice(6, 10))
LABELS = ("持续升高", "冲高回落", "持续走低", "起伏/其他")
FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"


def group_close_maxima(closes: list[float]) -> tuple[float, float, float]:
    if len(closes) != 10:
        raise ValueError("Opening shape requires ten closes")
    return tuple(max(closes[group]) for group in GROUPS)


def classify_group_maxima(maxima: tuple[float, float, float]) -> str:
    first, middle, last = maxima
    if first < middle < last:
        return "持续升高"
    if first > middle > last:
        return "持续走低"
    if middle > first and middle > last:
        return "冲高回落"
    return "起伏/其他"


def selected_top1() -> pd.DataFrame:
    trades = pd.read_csv(TRADES, dtype={"symbol6": str})
    trades = trades[
        trades.selection_rank.eq(1)
        & trades.signal_date.between("2025-05-01", "2025-07-31")
        & trades.policy.eq("只去掉14:40涨停ST")
    ].sort_values("signal_date")
    if len(trades) != 60 or trades.next_date.duplicated().any():
        raise ValueError("Expected 60 distinct May–July 2025 Top1 mornings")
    return trades


def first_ten_bars(day: str, code: str, root: Path = COMPACT_ROOT_2025) -> pd.DataFrame:
    bars = pd.read_csv(day_file(root, day),
                       usecols=["time", "symbol", "open", "high", "low",
                                "close", "volume", "turnover"],
                       dtype={"time": str, "symbol": str})
    bars = bars[bars.time.str[-8:].between("09:31:00", "09:40:00")]
    bars = bars[bars.symbol.str[:6].eq(code)].sort_values("time").reset_index(drop=True)
    times = [f"{day} 09:{minute:02d}:00" for minute in range(31, 41)]
    if bars.time.tolist() != times:
        raise ValueError(f"Missing exact opening bars: {day} {code}")
    if not ((bars.high >= bars[["open", "close", "low"]].max(axis=1)).all()
            and (bars.low <= bars[["open", "close", "high"]].min(axis=1)).all()
            and bars[["volume", "turnover"]].ge(0).all().all()):
        raise ValueError(f"Invalid OHLCVA: {day} {code}")
    return bars


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def draw_panel(draw: ImageDraw.ImageDraw, bars: pd.DataFrame, rec: dict,
               box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=12, fill="#ffffff", outline="#d1d5db", width=2)
    title = (f"{rec['next_date']}  {rec['symbol6']} {rec['name']}  "
             f"{rec['shape_label']}")
    draw.text((left + 14, top + 10), title, fill="#111827", font=font(18))
    draw.text((left + 14, top + 36),
              f"3/3/4 group max close: {rec['g1_max_close']:.2f}  "
              f"{rec['g2_max_close']:.2f}  {rec['g3_max_close']:.2f}",
              fill="#4b5563", font=font(14))
    chart_left, chart_right = left + 36, right - 18
    price_top, price_bottom = top + 72, bottom - 87
    vol_top, vol_bottom = bottom - 72, bottom - 33
    min_price = float(bars.low.min())
    max_price = float(bars.high.max())
    pad = max((max_price - min_price) * .1, min_price * .001)
    min_price -= pad
    max_price += pad
    def y_price(value: float) -> float:
        return price_bottom - (value - min_price) / (max_price - min_price) * (price_bottom - price_top)
    xs = [chart_left + (chart_right - chart_left) * (i + .5) / 10 for i in range(10)]
    for i in (3, 6):
        x = chart_left + (chart_right - chart_left) * i / 10
        draw.line((x, price_top, x, vol_bottom), fill="#9ca3af", width=2)
    max_vol = max(float(bars.volume.max()), 1)
    for i, row in enumerate(bars.itertuples(index=False)):
        x = xs[i]
        color = "#dc2626" if row.close >= row.open else "#16a34a"
        draw.line((x, y_price(row.high), x, y_price(row.low)), fill=color, width=2)
        y1, y2 = y_price(row.open), y_price(row.close)
        draw.rectangle((x - 8, min(y1, y2), x + 8, max(y1 + 2, y2 + 2)),
                       fill=color, outline=color)
        height = float(row.volume) / max_vol * (vol_bottom - vol_top)
        draw.rectangle((x - 8, vol_bottom - height, x + 8, vol_bottom), fill=color)
        draw.text((x - 15, bottom - 29), f"{31+i}", fill="#374151", font=font(13))
    for group_index, (start, end) in enumerate(((0, 3), (3, 6), (6, 10)), start=1):
        price = rec[f"g{group_index}_max_close"]
        group_x = (xs[start] + xs[end-1]) / 2
        draw.text((group_x - 22, y_price(price) - 18), f"G{group_index}",
                  fill="#1d4ed8", font=font(13))
    draw.text((right - 170, top + 40), "09:36 | prediction time", fill="#7c3aed",
              font=font(13))


def render_gallery(rows: pd.DataFrame, bar_map: dict[tuple[str, str], pd.DataFrame]) -> list[str]:
    chosen = []
    for label in LABELS:
        group = rows[rows.shape_label.eq(label)]
        chosen.extend(group.sample(min(4, len(group)), random_state=20261011)[
            ["next_date", "symbol6"]].itertuples(index=False, name=None))
    selected = rows.set_index(["next_date", "symbol6"]).loc[chosen].reset_index()
    width, panel_w, panel_h = 1460, 710, 320
    height = 70 + ((len(selected) + 1) // 2) * (panel_h + 12)
    image = Image.new("RGB", (width, height), "#f3f4f6")
    draw = ImageDraw.Draw(image)
    draw.text((18, 12), "次晨前 10 根一分钟 K：随机分层样本；红涨绿跌，下方为成交量",
              fill="#111827", font=font(22))
    draw.text((18, 41), "竖线分隔 3/3/4；分类使用每组最高的收盘价。09:36 时只能看到前 6 根。",
              fill="#4b5563", font=font(15))
    for index, rec in enumerate(selected.to_dict("records")):
        col, row = index % 2, index // 2
        x = 14 + col * (panel_w + 12)
        y = 70 + row * (panel_h + 12)
        draw_panel(draw, bar_map[(rec["next_date"], rec["symbol6"])], rec,
                   (x, y, x + panel_w, y + panel_h))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "random_top1_gallery.png"
    image.save(path)
    selected.to_csv(OUT / "random_top1_labels.csv", index=False)
    return [f"{row.next_date} {row.symbol6} {row.shape_label}"
            for row in selected.itertuples(index=False)]


def main() -> None:
    records = []
    bar_map: dict[tuple[str, str], pd.DataFrame] = {}
    for trade in selected_top1().itertuples(index=False):
        bars = first_ten_bars(trade.next_date, trade.symbol6)
        closes = bars.close.astype(float).tolist()
        maxima = group_close_maxima(closes)
        records.append({
            "signal_date": trade.signal_date,
            "next_date": trade.next_date,
            "symbol6": trade.symbol6,
            "name": trade.name,
            "g1_max_close": maxima[0],
            "g2_max_close": maxima[1],
            "g3_max_close": maxima[2],
            "shape_label": classify_group_maxima(maxima),
            "first6_close": ";".join(f"{value:.4f}" for value in closes[:6]),
            "full10_close": ";".join(f"{value:.4f}" for value in closes),
            "signal_price": trade.signal_price,
            "close_40_return_pct": (closes[-1] / trade.signal_price - 1) * 100,
        })
        bar_map[(trade.next_date, trade.symbol6)] = bars
    rows = pd.DataFrame(records)
    OUT.mkdir(parents=True, exist_ok=True)
    rows.to_csv(OUT / "all_top1_labels.csv", index=False, float_format="%.6f")
    chosen = render_gallery(rows, bar_map)
    summary = {"selected_top1": len(rows), "class_counts": rows.shape_label.value_counts().to_dict(),
               "random_gallery": chosen}
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
