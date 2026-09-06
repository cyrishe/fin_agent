from scripts.eval_finance_rest_detail import payload_problems


def test_empty_success_is_not_an_evaluation_failure():
    payload = {"ok":True, "summary":None, "data":{"results":[]}, "detail":{
        "turns":1, "steps":[{"kind":"llm", "duration_ms":10, "usage":{"output_tokens":4}}]}}
    assert payload_problems(payload,200) == []
    payload["detail"]["steps"][0]["duration_ms"] = None
    assert "model_step_telemetry_incomplete" in payload_problems(payload,200)


def test_failure_or_missing_detail_stops_evaluation():
    assert "request_failed" in payload_problems({"ok":False},502)
    assert "detail_missing" in payload_problems({"ok":True},200)
