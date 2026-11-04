import json

from scripts.eval_finance_report_mcp import REPORT_SOURCES, load_report_cases


def test_report_corpus_includes_increment_without_general_api_cases(tmp_path):
    for source, case_id in zip(REPORT_SOURCES, ['RTE001', 'RTEF001']):
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'cases': [{'case_id': case_id, 'question': 'test'}]}))
    cases = load_report_cases(tmp_path)
    assert [c['case_id'] for c in cases] == ['RTE001', 'RTEF001']
    assert [c['source_file'] for c in cases] == list(REPORT_SOURCES)
