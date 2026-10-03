from scripts.eval_finance_rest_detail import payload_problems, blocking_problems


def test_empty_success_is_not_an_evaluation_failure():
    payload = {"ok":True, "summary":None, "data":{"results":[]}, "detail":{
        "turns":1, "steps":[{"kind":"llm", "duration_ms":10, "usage":{"output_tokens":4}}]}}
    assert payload_problems(payload,200) == []
    payload["detail"]["steps"][0]["duration_ms"] = None
    assert "model_step_telemetry_incomplete" in payload_problems(payload,200)


def test_failure_or_missing_detail_stops_evaluation():
    assert "request_failed" in payload_problems({"ok":False},502)
    assert "detail_missing" in payload_problems({"ok":True},200)


def test_review_mode_preserves_query_failure_but_does_not_block():
    result = {"http_status":502, "response":{"error":{"code":"finance_query_failed"}}, "problems":["request_failed"]}
    assert blocking_problems(result) == ["request_failed"]
    assert blocking_problems(result, True) == []
    assert result["problems"] == ["request_failed"]
    result["problems"].append("detail_missing")
    assert blocking_problems(result, True) == ["detail_missing"]
    assert blocking_problems({"problems":["transport_error"]}, True) == ["transport_error"]
    assert blocking_problems({"http_status":200,"response":{"error":None},"problems":[]},True) == []


def test_comparison_preserves_first_entry_and_unique_methods():
    from scripts.summarize_finance_rest_detail import calls_info
    entries, apis, requests = calls_info([
        {"tool":"read_finance_catalog","subject":"stock","dataview":"report"},
        {"tool":"read_finance_catalog","subject":"stock","dataview":"basic_info"},
        {"tool":"finance_query","submitted_request":"result = stock.report(filter = \"code = 1\") -> title"},
        {"tool":"finance_query","submitted_request":"result = stock.report(filter = \"code = 2\") -> title"},
    ])
    assert entries == ["stock.report","stock.basic_info"]
    assert apis == ["stock.report"]
    assert len(requests) == 2
