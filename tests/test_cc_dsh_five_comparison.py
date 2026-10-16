import json

from scripts import summarize_cc_reference_dsh_five as report


def test_row_comparison_ignores_order_but_preserves_duplicates():
    first = [{"code": "A", "weight": None}, {"code": "B", "weight": None}]
    assert report.canonical(first) == report.canonical(list(reversed(first)))
    assert report.canonical(first) != report.canonical(first + [first[0]])
    assert report.canonical(first) <= report.canonical(first + [{"code": "C", "weight": None}])


def test_cc_native_usage_deduplicates_message_fragments_and_includes_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "OUTPUT", tmp_path)
    monkeypatch.setattr(report, "ROOT", tmp_path)
    folder = tmp_path / "cc/workspace/data/financial_qa_cc_sessions"
    folder.mkdir(parents=True)
    usage = {"input_tokens": 100, "output_tokens": 25, "cache_read_input_tokens": 200,
             "cache_creation_input_tokens": 10}
    event = {"type": "assistant", "message": {"id": "message-1", "usage": usage}}
    (folder / "session-1.jsonl").write_text(json.dumps(event) + "\n" + json.dumps(event) + "\n")
    actual = report.native_cc_usage({"financial_qa": {"session_id": "session-1"},
                                    "llm_usage": {"prompt_tokens": 100, "completion_tokens": 25}})
    assert actual["llm_requests"] == 1
    assert actual["tokens_with_cache"] == 335
