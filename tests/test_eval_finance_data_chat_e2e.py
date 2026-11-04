from scripts.eval_finance_data_chat_e2e import (
    _FixtureFinanceRuntime,
    _progress_quality,
    _scope_application_context,
)


def test_fixture_does_not_invent_values_for_unmodeled_fields() -> None:
    entity = {"code": "600519.SH", "name": "贵州茅台"}

    assert _FixtureFinanceRuntime._value("close", entity=entity) == 1500.0
    assert _FixtureFinanceRuntime._value(
        "unmodeled_supported_field",
        entity=entity,
    ) is None


def test_fixture_returns_every_explicitly_requested_entity() -> None:
    runtime = _FixtureFinanceRuntime()

    result = runtime.execute_request(
        request=(
            'r1 = stock.pricevalue(filter = "code in '
            "('600519.SH','000858.SZ')\", realtime = 0) "
            "-> code, name, pe, pb"
        )
    )

    assert result["result"]["data"]["rows"] == [
        {"code": "600519.SH", "name": "贵州茅台", "pe": 23.5, "pb": 7.2},
        {"code": "000858.SZ", "name": "五粮液", "pe": 23.5, "pb": 7.2},
    ]


def test_finance_query_eval_scope_removes_overlapping_configured_tools() -> None:
    context = {
        "default_agent": {
            "tools": ["stock_realtime_quote"],
            "runtime_profile": {"tools": ["stock_realtime_quote"]},
        },
        "available_agents": [
            {
                "tools": ["stock_realtime_quote"],
                "runtime_profile": {"tools": ["stock_realtime_quote"]},
            }
        ],
    }

    scoped = _scope_application_context(
        context,
        tool_scope="finance_query_only",
    )

    assert scoped["default_agent"]["tools"] == []
    assert scoped["default_agent"]["runtime_profile"]["tools"] == []
    assert scoped["available_agents"][0]["tools"] == []
    assert scoped["available_agents"][0]["runtime_profile"]["tools"] == []
    assert context["default_agent"]["tools"] == ["stock_realtime_quote"]


def test_progress_quality_distinguishes_public_milestones_from_hidden_protocol() -> None:
    quality = _progress_quality([
        {
            "elapsed_ms": 10,
            "content": "mcp__finance__finance_query",
            "metadata": {"user_visible": False},
        },
        {
            "elapsed_ms": 20,
            "content": "正在查询融资余额。",
            "metadata": {"progress_id": "finance_query_step_1"},
        },
        {
            "elapsed_ms": 30,
            "content": "闭环判断：sample_complete=true",
            "metadata": {},
        },
    ])

    assert quality == {
        "visible_progress_count": 2,
        "meaningful_progress_count": 1,
        "internal_leak_count": 1,
        "first_visible_progress_ms": 20,
        "first_meaningful_progress_ms": 20,
    }
