"""Frozen B2 V2 strategy definition and month-start pause decision.

V2 reuses B2's monthly models, four-K rules, D1 entry and D2 exit. The only
new decision is whether D0 signals of an entire model month are allowed.
"""
from __future__ import annotations

import pandas as pd

from scripts.automl_b2 import definition as b2_definition, digest


STRATEGY_ID = "B2_V2"
DISPLAY_NAME = "涨停冷静 V2 - 月度停摆"
BASE_STRATEGY_ID = "B2"
BASE_DEFINITION_SHA256 = "157b9d77bb9951214b0978365cec407320b1d5663636b80fe60939ae94172c0c"
FROZEN_DEFINITION_SHA256 = "54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00"


def definition() -> dict:
    base = b2_definition()
    if digest(base) != BASE_DEFINITION_SHA256:
        raise ValueError("B2 V1 基础定义哈希改变，V2 不能隐式继承")
    overlay = (
        "目标D0月开始前，以始终照原B2交易的影子账户计算上个自然月最后5、10个交易日"
        "收盘净值收益；每个窗口均以上一交易日净值为分母。两项收益之和严格小于0，"
        "或上月D0最终候选为0，则目标月D0新信号整月停摆；否则整月放行。"
        "没有更早可用历史不视作零候选，月中不恢复，影子账户始终继续交易。"
        "决策按D0模型月归属；跨月D1/D2继续沿用D0月日线模型和四K规则。"
        "月末D0只计入候选数，其尚未发生的交易收益不进入月末影子净值；"
        "已买未卖持仓按当日收盘估值。"
    )
    return {
        "title": DISPLAY_NAME,
        "summary": "原B2月模型、四K与D1买入/D2卖出，加上前月影子净值整月停摆判断。",
        "training_method": base["training_method"],
        "selection_formula": base["selection_formula"] + " " + overlay,
        "execution_formula": base["execution_formula"],
        "formula": {
            "version": "B2_V2_MONTHLY_PAUSE_20261020",
            "base_strategy_id": BASE_STRATEGY_ID,
            "base_definition_sha256": BASE_DEFINITION_SHA256,
            "base_formula": base["formula"],
            "monthly_pause": overlay,
        },
    }


def month_decision(target_month: str, prior_candidates: int | None,
                   shadow_nav: pd.DataFrame) -> dict:
    """Decide from prior-month closing NAV; index=date, column=nav.

    `prior_candidates=None` means no earlier model history. Zero is a known
    zero-candidate month. Input NAV must be chronological and unique.
    """
    prior = str(pd.Period(target_month, freq="M") - 1)
    if prior_candidates is not None and prior_candidates < 0:
        raise ValueError("前月D0候选数不能为负")
    if not shadow_nav.index.is_unique or not shadow_nav.index.is_monotonic_increasing:
        raise ValueError("影子净值日期必须唯一且递增")
    if (shadow_nav.nav <= 0).any() or shadow_nav.nav.isna().any():
        raise ValueError("影子净值必须为正且完整")
    dates = shadow_nav.index[shadow_nav.index.str.startswith(prior)].tolist()

    def last_return(count: int) -> float | None:
        if len(dates) < count:
            return None
        first = shadow_nav.index.get_loc(dates[-count])
        if first == 0:
            return None
        return float(shadow_nav.loc[dates[-1], "nav"] / shadow_nav.iloc[first - 1].nav - 1)

    r5, r10 = last_return(5), last_return(10)
    pause = bool(prior_candidates == 0 or (
        prior_candidates is not None and r5 is not None and r10 is not None
        and r5 + r10 < 0
    ))
    return dict(model_month=target_month, prior_month=prior,
                prior_D0_candidates=prior_candidates, prior_last5_return=r5,
                prior_last10_return=r10, pause=pause)
