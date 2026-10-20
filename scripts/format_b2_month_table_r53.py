"""Join frozen R52/R53 monthly evidence into a short, reader-facing table."""
from __future__ import annotations

import pandas as pd
from pathlib import Path

OUT = Path("docs/stock_automl_runs/20261020_b2_no_shape_last_valid_r53")
R52 = Path("docs/stock_automl_runs/20261020_b2_validation_vs_month_r52")


def rate(value: float) -> str:
    return "—" if pd.isna(value) else f"{value:.1%}"


def ret(value: float) -> str:
    return "—" if pd.isna(value) else f"{value:+.2%}"


def hit(value: float, count: int) -> str:
    return f"{rate(value)}（{int(count)}）"


def trade(win: float, mean: float, count: int) -> str:
    return "—（0）" if count == 0 else f"{rate(win)}／{ret(mean)}（{int(count)}）"


def main() -> None:
    old = pd.read_csv(R52 / "31月逐月对照.csv")
    new = pd.read_csv(OUT / "31月无形态与末月验证.csv")
    assert len(old) == len(new) == 31
    joined = old.merge(new, left_on="月份", right_on="测试月", validate="one_to_one")
    assert joined.月份.is_unique
    rows = []
    for r in joined.itertuples():
        rows.append({
            "测试月": r.月份,
            "前两月验证期": r.验证期,
            "日线 Top1": rate(r.日线验证Top1),
            "四 K 再板率": hit(r.四K验证再板率, r.四K验证样本),
            "验证期 D2 费后胜率／笔均": trade(r.验证期D2费后胜率, r.验证期D2费后笔均,
                                         r.验证期延迟可买笔),
            "测试月实买 D2 胜率／笔均": trade(r.测试月D2费后胜率, r.测试月D2费后笔均,
                                          r.测试月D0已卖笔),
            "月收益": ret(r.测试月账户收益),
            "末月日线 Top1／Top3": f"{hit(r.验证末月日线Top1再板率, r.验证末月日线Top1样本)}／"
                                  f"{hit(r.验证末月日线Top3再板率, r.验证末月日线Top3样本)}",
            "末月无四 K Top1 D2 胜率／笔均": trade(r.验证末月无形态Top1胜率,
                                                r.验证末月无形态Top1笔均,
                                                r.验证末月无形态Top1笔),
            "测试月无四 K Top3 D2 胜率／笔均": trade(r.测试无形态Top3D2胜率,
                                                  r.测试无形态Top3D2笔均,
                                                  r.测试无形态Top3笔),
            "测试月无四 K／原 B2 同起账月收益":
                f"{ret(r.测试无形态Top3独立月账户)}／{ret(r.测试原B2独立月账户)}",
        })
    table = pd.DataFrame(rows).sort_values("测试月")
    table.to_csv(OUT / "31月简表_原口径加末月及无四K.csv", index=False, encoding="utf-8-sig")
    print(table[table.测试月.isin(["2024-04", "2024-08", "2024-10", "2026-09"])].to_string(index=False))


if __name__ == "__main__":
    main()
