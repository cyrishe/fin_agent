from src.services.design_narrative_service import compose_design_narrative


def test_vague_goal_is_acknowledged_without_inventing_design_details() -> None:
    text = compose_design_narrative(
        {"goal": "创建一个选股工具", "expected_result": "输出候选股票"},
        [{"id": "rule"}, {"id": "universe"}],
        {"display_name": "选股工具", "inputs": [], "outputs": [], "rules": []},
    )

    assert text.startswith("明白，我们先从“创建一个选股工具”这个方向开始。")
    assert "先不替你假定具体规则、数据范围或返回形式" in text
    assert "2 个真正决定工具形态的问题" in text


def test_partial_design_narrates_known_facts_before_remaining_questions() -> None:
    text = compose_design_narrative(
        {
            "goal": "判断最近 30 或 60 个交易日内是否出现金叉",
            "expected_result": "返回是否出现及发生日期",
            "confirmed_requirements": ["使用日线收盘价", "同时检查 30 日和 60 日窗口"],
        },
        [{"id": "ma_period"}],
        {"rules": [{"name": "金叉判断"}]},
    )

    assert text.startswith("按我的理解，这个工具要解决的是“判断最近 30 或 60 个交易日内是否出现金叉”。")
    assert "最终结果会是“返回是否出现及发生日期”。" in text
    assert "你已经明确了这些要求：使用日线收盘价；同时检查 30 日和 60 日窗口。" in text
    assert "还有 1 个会影响结果的关键点" in text


def test_review_ready_design_invites_goal_level_review() -> None:
    text = compose_design_narrative(
        {"goal": "判断市场状态", "expected_result": "输出趋势状态"},
        [],
        {"rules": [{"name": "趋势规则"}]},
    )

    assert "下面是按这个目标整理的核心计算和处理路径" in text
    assert "需要补充信息" not in text
