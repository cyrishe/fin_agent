"""Summarize daily deep-V labels from the saved, read-only sample audit.

This only reads the outputs of explore_automl_deep_v_samples.py. It neither
queries the database nor trains or tests a trading model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


DEFAULT_DATA = Path("outputs/stock_automl/deep_v_20261013")
DEFAULT_FIGURE = Path("docs/stock_automl_runs/20261013_deep_v_samples/daily_rates.png")
DEFAULT_TABLE = Path("docs/stock_automl_runs/20261013_deep_v_samples/DAILY.md")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    args = parser.parse_args()

    daily = pd.read_csv(args.data / "daily_coverage.csv")
    daily = daily.loc[daily.events.gt(0)].copy()
    for label, count in (("class_1_pct", "class_1"),
                         ("class_0_pct", "class_0"),
                         ("class_minus1_pct", "class_minus1")):
        daily[label] = 100 * daily[count] / daily.events
    daily["event_share_of_covered_pct"] = 100 * daily.events / daily.full_exact_stocks
    daily["coverage_pct"] = 100 * daily.full_exact_stocks / daily.minute_stocks

    windows = pd.read_csv(args.data / "rolling20_coverage.csv")
    windows = windows.loc[windows.history_data_days.eq(20)].copy()
    windows["history_class_1_pct"] = 100 * windows.history_class_1 / windows.history_events
    windows["test_class_1_pct"] = 100 * windows.test_class_1 / windows.test_events
    windows["test_minus_history_pp"] = windows.test_class_1_pct - windows.history_class_1_pct
    windows.to_csv(args.data / "daily_vs_history20.csv", index=False)

    daily = daily.merge(windows[["test_day", "history_class_1_pct"]],
                        how="left", left_on="day", right_on="test_day")
    daily = daily.drop(columns="test_day")
    daily.to_csv(args.data / "daily_rates.csv", index=False)

    lines = ["# 深 V 样本逐日分布", "",
             "口径、数据质量条件见 [D1 主报告](README.md)。`1/0/-1` 的百分比均以当日振幅超过 5% 的完整分钟股票日为分母；前 20 日列是 T−20…T−1 合计比例，不包含 T 日。8 月 3、4 日无完整分钟数据，所以表中从 8 月 5 日开始，前 20 日列从 9 月 2 日开始。", "",
             "![逐日 1 类占比与前 20 日基准](daily_rates.png)", ""]
    for month, group in daily.groupby(daily.day.str[:7], sort=True):
        lines.extend([f"## {month}", "",
                      "| 日期 | 完整分钟／有分钟股票 | 振幅>5% | 1：收盘≥高点−1% | 0：−3%～−1% | −1：低于−3% | 前20日1类占比 |",
                      "|---|---:|---:|---:|---:|---:|---:|"])
        for row in group.itertuples():
            history = ("—" if pd.isna(row.history_class_1_pct)
                       else f"{row.history_class_1_pct:.1f}%")
            lines.append(
                f"| {row.day} | {row.full_exact_stocks:,}/{row.minute_stocks:,} | "
                f"{row.events:,} | {row.class_1:,} ({row.class_1_pct:.1f}%) | "
                f"{row.class_0:,} ({row.class_0_pct:.1f}%) | "
                f"{row.class_minus1:,} ({row.class_minus1_pct:.1f}%) | {history} |"
            )
        lines.append("")
    lines.extend(["原始派生数据：[逐日明细](../../../outputs/stock_automl/deep_v_20261013/daily_rates.csv)、[T 日与前 20 日对照](../../../outputs/stock_automl/deep_v_20261013/daily_vs_history20.csv)。8 月 25、28 日及 10 月 9、12 日分钟覆盖不足 95%，图中以橙点标记；其余日期仍可能有少量股票缺完整分钟数据。", ""])
    args.table.parent.mkdir(parents=True, exist_ok=True)
    args.table.write_text("\n".join(lines), encoding="utf-8")

    width, height = 1900, 860
    im = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(im)
    font_file = "/System/Library/Fonts/Supplemental/Arial.ttf"
    font = ImageFont.truetype(font_file, 23)
    small = ImageFont.truetype(font_file, 18)
    title = ImageFont.truetype(font_file, 32)
    left, right, top, bottom = 100, width - 65, 115, height - 175
    plot_h = bottom - top
    draw.text((left, 34), "Deep-V share varies sharply by trading day", font=title, fill="#202a33")
    draw.text((left, 76), "Class 1 / all stocks with >5% intraday close-price range", font=font,
              fill="#546475")
    for percent in range(0, 71, 10):
        y = bottom - plot_h * percent / 70
        draw.line((left, y, right, y), fill="#dfe5e9", width=2)
        draw.text((left - 62, y - 12), f"{percent}%", font=small, fill="#617381")

    step = (right - left) / (len(daily) - 1)
    xs = [left + i * step for i in range(len(daily))]
    ys = [bottom - plot_h * pct / 70 for pct in daily.class_1_pct]
    for i in range(1, len(xs)):
        if daily.iloc[i].day[:7] != daily.iloc[i - 1].day[:7]:
            x = (xs[i - 1] + xs[i]) / 2
            draw.line((x, top, x, bottom), fill="#a9b5bd", width=2)
            draw.text((x + 10, top + 12), daily.iloc[i].day[:7], font=small,
                      fill="#617381")

    # The grey line uses only the 20 sessions preceding the plotted T day.
    for i in range(1, len(daily)):
        a, b = daily.iloc[i - 1], daily.iloc[i]
        if pd.notna(a.history_class_1_pct) and pd.notna(b.history_class_1_pct):
            ya = bottom - plot_h * a.history_class_1_pct / 70
            yb = bottom - plot_h * b.history_class_1_pct / 70
            draw.line((xs[i - 1], ya, xs[i], yb), fill="#68737c", width=4)
    for i in range(1, len(daily)):
        draw.line((xs[i - 1], ys[i - 1], xs[i], ys[i]), fill="#138467", width=4)
    for i, row in enumerate(daily.itertuples()):
        partial = row.coverage_pct < 95
        radius = 8 if not partial else 10
        color = "#df832c" if partial else "#138467"
        draw.ellipse((xs[i] - radius, ys[i] - radius,
                      xs[i] + radius, ys[i] + radius), fill=color,
                     outline="#ffffff", width=2)
        if i % 3 == 0 or i == len(daily) - 1:
            draw.text((xs[i] - 23, bottom + 18), row.day[5:], font=small,
                      fill="#617381")

    legend_y = height - 78
    draw.line((left, legend_y, left + 45, legend_y), fill="#138467", width=5)
    draw.text((left + 57, legend_y - 12), "T-day class 1", font=font, fill="#25313b")
    draw.line((left + 330, legend_y, left + 375, legend_y), fill="#68737c", width=5)
    draw.text((left + 388, legend_y - 12), "Prior 20-day pooled rate", font=font,
              fill="#25313b")
    draw.ellipse((left + 760, legend_y - 9, left + 778, legend_y + 9), fill="#df832c")
    draw.text((left + 790, legend_y - 12), "<95% full-minute coverage", font=font,
              fill="#25313b")
    args.figure.parent.mkdir(parents=True, exist_ok=True)
    im.save(args.figure)
    print(f"{len(daily)} days; {len(windows)} complete 20-day windows; "
          f"{args.figure}; {args.table}")


if __name__ == "__main__":
    main()
