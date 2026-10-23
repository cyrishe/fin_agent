"""Join frozen V2 opening-floor paths to costs and R69 closing-floor cohorts.

The opening-price returns are counterfactual price diagnostics, not fills.
No model, trading rule, account, or production record is changed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean

BASE = Path("docs/stock_automl_runs")
PATHS = {
    "r70": BASE / "20261021_b2_floor_minutes_r70/results.json",
    "r69": BASE / "20261021_b2_d2_limit_follow_r69/results.json",
    "ledger": BASE / "20261020_b2_model_month_r61/模型归属.json",
}
OUT = BASE / "20261021_b2_open_floor_intersection"
FEE = .001


def key(row):
    return row["D0"], row["股票"][:6]


def stats(rows):
    return {
        "n": len(rows),
        "hypothetical_open_losses": sum(x["hypothetical_open_net_pct"] < -1e-9 for x in rows),
        "hypothetical_open_wins": sum(x["hypothetical_open_net_pct"] > 1e-9 for x in rows),
        "hypothetical_open_mean_net_pct": mean(x["hypothetical_open_net_pct"] for x in rows),
        "original_close_mean_net_pct": mean(x["original_close_net_pct"] for x in rows),
        "original_close_losses": sum(x["original_close_net_pct"] < -1e-9 for x in rows),
        "close_above_open": sum(x["D2收盘"] > x["D2开盘"] + .001 for x in rows),
        "close_equal_open": sum(abs(x["D2收盘"] - x["D2开盘"]) < .001 for x in rows),
        "open_loss_close_profit": sum(x["hypothetical_open_net_pct"] < -1e-9 and x["original_close_net_pct"] > 1e-9 for x in rows),
    }


def main():
    inputs = {k: json.loads(p.read_text()) for k, p in PATHS.items()}
    ledger = {key(x): x for x in inputs["ledger"]["逐笔"]}
    sealed = {key(x): x for x in inputs["r69"]["rows"]}
    all_rows = inputs["r70"]["rows"]
    opening = [dict(x) for x in all_rows if x["V2放行"] == "是" and x["开盘跌停"] == "是"]
    failed49 = {key(x) for x in all_rows if x["49笔组"] == "是"}
    closefloor52 = {key(x) for x in inputs["r69"]["rows"] if x["V2放行"] == "是" and x["封板"] == "跌停"}
    assert len(ledger) == 306 and len(opening) == 34 and len(failed49) == 49 and len(closefloor52) == 52
    assert len({key(x) for x in opening}) == 34
    for i, row in enumerate(sorted(opening, key=key), 1):
        old = ledger[key(row)]
        assert row["D2"] == old["卖出日"]
        assert abs(row["D2收盘"] - old["卖价"]) < 1e-8
        assert abs(row["D2开盘"] - row["跌停价"]) < .001
        row.update(sequence=i, entry_price=old["买价"],
                   hypothetical_open_net_pct=100 * (row["D2开盘"] / old["买价"] * (1-FEE) / (1+FEE) - 1),
                   original_close_net_pct=100 * old["费后收益"],
                   close_vs_open_pct=100 * (row["D2收盘"] / row["D2开盘"] - 1))
        close_return = 100 * (row["D2收盘"] / old["买价"] * (1-FEE) / (1+FEE) - 1)
        assert abs(close_return - row["original_close_net_pct"]) < 1e-7
        if key(row) in sealed:
            assert abs(sealed[key(row)]["原笔净收益_%"] - row["original_close_net_pct"]) < 1e-7
        assert row["上午7%"] == "否"
        row["path_group"] = ("全天封死" if row["全天全封"] == "是" else
                             "打开后收盘又跌停" if row["收盘跌停"] == "是" else "收盘离开跌停")
    opening.sort(key=lambda x: x["sequence"])
    keys = {key(x) for x in opening}
    intersection = {
        "opening_and_failed49": len(keys & failed49),
        "opening_not_failed49": len(keys - failed49),
        "failed49_not_opening": len(failed49 - keys),
        "opening_and_closefloor52": len(keys & closefloor52),
        "opening_and_all_day_locked": sum(x["全天全封"] == "是" for x in opening),
        "opening_and_afternoon_locked": sum(x["下午全封"] == "是" for x in opening),
    }
    assert list(intersection.values()) == [19, 15, 30, 19, 14, 15]
    groups = {
        "all_opening_floor": opening,
        "all_day_locked": [x for x in opening if x["全天全封"] == "是"],
        "opened_during_day": [x for x in opening if x["全天全封"] == "否"],
        "opened_then_closed_floor": [x for x in opening if x["path_group"] == "打开后收盘又跌停"],
        "close_recovered_from_floor": [x for x in opening if x["path_group"] == "收盘离开跌停"],
    }
    summary = {name: stats(rows) for name, rows in groups.items()}
    next_day = {}
    for name, rows in [("opening_and_closing_floor", [x for x in opening if key(x) in closefloor52]),
                       ("opened_then_closed_floor", groups["opened_then_closed_floor"])]:
        records = [sealed[key(x)] for x in rows]
        assert all(x["次开_%"] is not None and x["次收_%"] is not None for x in records)
        next_day[name] = {"n": len(records), "next_open_mean_pct": mean(x["次开_%"] for x in records),
                          "next_close_mean_pct": mean(x["次收_%"] for x in records),
                          "next_open_down_count": sum(x["次开_%"] < 0 for x in records)}
    assert summary["all_opening_floor"]["hypothetical_open_losses"] == 33
    assert summary["opened_during_day"]["n"] == 20
    assert summary["opened_during_day"]["hypothetical_open_losses"] == 20
    assert summary["opened_then_closed_floor"]["n"] == 5
    result = {
        "experiment": "B2_OPEN_FLOOR_INTERSECTION_20261021", "scope": "Frozen V2 306 trades, D0 2022-05 through 2026-09",
        "fee_per_side": FEE, "intersection": intersection, "groups": summary,
        "next_day_price_diagnostics": next_day, "rows": opening,
        "boundaries": ["Opening returns assume a fill at D2 opening floor; no execution claimed.",
                       "Actual first available minute sale not computed; 14 full-day locks cannot be counted as executed.",
                       "Equal-weight trade diagnostics, not capital-weighted or continuous account returns.",
                       "Frozen V2 gate retained for cohort selection; no shadow feedback recomputation."],
        "sources": {k: {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for k, p in PATHS.items()},
        "verification": {"rows_joined": 34, "D2_dates_and_close_prices_match": True,
                         "original_net_return_reproduced": True, "production_modified": False},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    total = summary["all_opening_floor"]
    report = ["# 开盘跌停与 R69/R70 的交集及开盘价卖出诊断", "",
        "范围：冻结 V2 的 306 笔，D0 2022-05—2026-09。用 R70 的开盘跌停事实、R61 原买价与原 D2 卖价、R69 后续价格逐笔关联。没有新增训练、实际成交模拟或账户回测。", "",
        "## 交集", "",
        "开盘跌停 34 笔，其中 19 笔属于前述‘收盘跌停且上午未触发7%’的49笔；占49笔的38.78%。其余30笔并非开盘跌停，单靠开盘跌停规则覆盖不到。34笔全部上午未触发7%。", "",
        "| 开盘跌停后的路径 | 笔数 | 可由现有证据说明的事实 |", "| --- | ---: | --- |",
        "| 全天封死 | 14 | 没有离板成交窗口；不能按开盘已卖出计账 |",
        "| 盘中打开，收盘再次跌停 | 5 | 有离板窗口，值得检查及时退出，但不能认定成交 |",
        "| 收盘离开跌停 | 15 | 收盘价均高于开盘价，开盘地板价卖出会错过回升 |", "",
        "19笔交集包含全天14笔、上午打开而下午全封1笔、下午曾打开4笔。49笔中余下30笔分别为全天0、上午打开下午全封8、下午打开22。", "",
        "## 卖掉会亏：分母为原买入成本", "",
        f"假设34笔全按D2开盘价成交，买卖各扣0.1%：33笔亏损（97.06%），1笔盈利；等权费后笔均 {total['hypothetical_open_mean_net_pct']:+.2f}%。原D2收盘价假设下，31笔亏损、3笔盈利，笔均 {total['original_close_mean_net_pct']:+.2f}%。这些不是可实现成交或账户收益。", "",
        "唯一开盘价仍盈利的603137.SH约+0.58%，但属于全天封死组，所以也不能算作真实卖出盈利。盘中曾打开的20笔，按开盘价假设全部亏损；其后按D2收盘价计18亏、2盈。", "",
        "若‘亏’指卖早了：34笔中15笔收盘价高于开盘价，19笔相同，没有更低；开盘已在当日跌停价，该比较本就不会出现收盘更低。这不能证明等待能卖出，更不能评价跨日风险。", "",
        "19笔开盘及收盘均跌停，下一市场日开盘全部更低，平均再跌7.88%；其中5笔盘中打开后重封，次开平均再跌6.60%。这是后续价格风险，不能默认下一日可成交。", "",
        "## 对降低风险的含义", "",
        "现有证据支持进一步检验‘开盘跌停后在可成交窗口尽早退出’，没有证明该策略能降低完整账户风险。全天14笔不能被这种规则直接救出；另外5笔可能避免继续被封住，但15笔会牺牲不同程度的当日反弹。且49笔里的30笔开盘并不跌停，仍需另行处理。", "",
        "下一项实际模拟应从开盘时已知条件触发，逐分钟检查订单执行代理，保留未成交持仓；不能用全天是否打开或收盘结果作为事前筛选条件。实际退出价可能高于开盘跌停价，所以本表的33笔亏损不等于‘打开后卖出也必然33笔亏损’。", "",
        "## 34笔明细（按时间顺序，以笔序展示）", "",
        "开盘价净收益列均为假设成交。底层 results.json 保存真实日期、来源哈希及交集键。", "",
        "| 笔序 | 股票 | 原买价 | D2开价 | D2收价 | 开价费后%（假设） | 收价费后%（原假设） | 路径 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for x in opening:
        report.append(f"| {x['sequence']} | {x['股票']} | {x['entry_price']:.2f} | {x['D2开盘']:.2f} | {x['D2收盘']:.2f} | {x['hypothetical_open_net_pct']:+.2f} | {x['original_close_net_pct']:+.2f} | {x['path_group']} |")
    report += ["", "复算：`python -m scripts.analyze_b2_open_floor_intersection_20261021`。输入为冻结 JSON，无数据库或服务器访问。34笔卖出日、买价关联、收盘价和费后公式已逐笔核对；没有修改生产定义、影子停摆门槛或原历史回执。"]
    (OUT / "README.md").write_text("\n".join(report) + "\n")
    print(json.dumps({"intersection": intersection, "groups": summary, "next_day": next_day}, ensure_ascii=False))


if __name__ == "__main__":
    main()
