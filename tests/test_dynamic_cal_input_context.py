import json

from src.experiments.staged_data_protocol.phase2 import dynamic_cal_provider as provider


def test_codegen_receives_actual_selected_window_and_date_type(monkeypatch):
    from src.utils import ai_service
    rows = [{'code': '000001.SZ', 'tradedate': '2026-09-28', 'volumn': 20.0},
            {'code': '000001.SZ', 'tradedate': '2026-09-29', 'volumn': 40.0}]
    monkeypatch.setattr(provider, '_load_quote_window_rows', lambda **kw: rows)
    prompts = []
    def generate(messages, **kwargs):
        prompts.append(messages[0]['content'])
        return json.dumps({'code': "def compute(df):\n    return df.groupby('code', as_index=False).agg(avg_volume=('volumn', 'mean'))",
            'output_schema': [{'var_name':'avg_volume','var_display_name':'平均成交量','var_desc':'已选窗口均量'}]}), None
    monkeypatch.setattr(ai_service, 'chat_qwen_flash', generate)
    result = provider.execute_dynamic_quote_api(subject='stock',
        args={'k':2, 'fields':'code, tradedate, volumn', 'task':'截至上一交易日的日均成交量'},
        outputs=['code','avg_volume'])
    assert result['status'] == 'ok'
    assert result['rows'] == [{'code':'000001.SZ','avg_volume':30.0}]
    assert '"input_rows": 2' in prompts[0]
    assert '"last_observed_date": "2026-09-29"' in prompts[0]
    assert 'ISO strings' in prompts[0] and 'already selected the input window' in prompts[0]
    assert '{{input_context}}' not in prompts[0]


def test_legacy_codegen_call_without_input_context_remains_valid(monkeypatch):
    from src.utils import ai_service
    monkeypatch.setattr(ai_service, 'chat_qwen_flash', lambda *a, **kw: (
        json.dumps({'code':'def compute(df):\n    return df', 'output_schema':[]}), None))
    assert provider._generate_compute_code(task='原样返回', columns=['code'], output_columns=['code'])['status'] == 'ok'
