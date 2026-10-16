"""Background-only adapter; the independent study engine owns research, not tasks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _result(output, plan_path, plan, study):
    from src.quant_research.automl.runner import model_display_name
    from src.quant_research.automl.study import render_study
    successful = [s for s in study['strategies'] if s.get('report')]
    qualified = sum(s['report']['development_constraints_met'] for s in successful)
    attempts = sum(s.get('attempted_trials', s.get('report', {}).get('attempted_trials', 0)) for s in study['strategies'])
    summary = (f"已研究 {len(study['strategies'])} 个方向，保存 {len(successful)} 个独立候选策略；"
               f"完成 {attempts} 个候选实验，其中 {qualified} 个方向满足开发筛选条件。"
               "各方向分别提供样本、解释和留出回测证据；未达标或无信号也是研究结果。")
    if study['source'] == 'demo':
        summary = '合成数据流程验证。' + summary
    artifacts = [{'name': '研究设计', 'path': str(plan_path), 'mime_type': 'application/json'},
                 {'name': '研究结果', 'path': str(output / 'study.md'), 'mime_type': 'text/markdown'},
                 {'name': '研究结果数据', 'path': str(output / 'study.json'), 'mime_type': 'application/json'}]
    public_names = ['report.md', 'report.json', 'review.md', 'review_status.json', 'review_evidence.json',
                    'sample_counts.json', 'development.json', 'selection.json', 'spec.json',
                    'strategy.json', 'explanation.json', 'explanation.md']
    for item in successful:
        root = (output / item['research_dir']).resolve()
        if not root.is_relative_to(output):
            raise ValueError('strategy artifact is outside the authorized task directory')
        artifacts.extend({'name': (item['id'] + ' · ' if len(study['strategies']) > 1 else '') + name, 'path': str(root / name),
                          'mime_type': 'text/markdown' if name.endswith('.md') else 'application/json'}
                         for name in public_names if (root / name).is_file())
    metrics = [{'label': '研究方向', 'value': len(study['strategies'])},
               {'label': '已保存策略', 'value': len(successful)},
               {'label': '开发达标方向', 'value': qualified},
               {'label': '候选实验数', 'value': attempts}]
    domain = {'design': plan['design'], 'spec': plan['spec'], 'strategies': study['strategies']}
    report_text = render_study(study)
    # Preserve the original single-direction result contract. A study has no global winner.
    if len(study['strategies']) == 1 and successful:
        item = successful[0]
        report = item['report']
        root = output / item['research_dir']
        domain.update({key: report[key] for key in ('sample_counts', 'selected', 'development_constraints_met', 'evaluation', 'limitations')})
        domain['review_status'] = json.loads((root / 'review_status.json').read_text())
        counts = report['sample_counts']
        metrics += [{'label': '候选模型', 'value': model_display_name(report['selected'])},
                    {'label': '持有周期', 'value': report['selected']['horizon'], 'unit': '交易日'},
                    {'label': '行情样本行数', 'value': counts['panel_rows']},
                    {'label': '股票数', 'value': counts['companies']},
                    {'label': '实际拟合行数', 'value': counts['development']['fit_rows']},
                    {'label': '概率校准行数', 'value': counts['development']['calibration_rows']}]
        for name, evidence in report['evaluation'].items():
            if 'prediction' not in evidence:
                continue
            label = '原公司新时段' if name == 'new_period' else '新公司新时段'
            prediction = evidence['prediction']
            for title, value in [('目标精确率', prediction.get('target_precision')),
                                 ('扣成本信号胜率', prediction['signal_win_rate_after_cost'])]:
                metrics.append({'label': label + title, 'value': round(value * 100, 2) if value is not None else '无信号',
                                'unit': '%' if value is not None else ''})
            metrics += [{'label': label + '信号数', 'value': prediction['signal_count']},
                        {'label': label + '组合收益', 'value': round(evidence['portfolio']['total_return'] * 100, 3), 'unit': '%'},
                        {'label': label + '组合最大回撤', 'value': round(evidence['portfolio']['max_drawdown'] * 100, 3), 'unit': '%'}]
        report_text += '\n\n' + (root / 'report.md').read_text()
    return {'tool': 'stock_automl_research', 'ok': True, 'summary': summary, 'report_markdown': report_text,
            'metrics': metrics, 'artifacts': artifacts, 'domain_result': domain}


def run(args, *, runtime_ctx=None):
    runtime = dict(runtime_ctx or {})
    if not runtime.get('task_run_id') or not runtime.get('owner_user_id') or not runtime.get('task_output_dir'):
        raise ValueError('stock_automl_research must run through an authorized background task')
    params = dict(args)
    params.pop('_runtime', None)
    unknown = set(params) - {'requirement_brief', 'spec', 'source', 'llm_review'}
    if unknown:
        raise ValueError('unsupported tool arguments: ' + ', '.join(sorted(unknown)))
    requirement = params.get('requirement_brief', '')
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError('requirement_brief is required')
    source_name = params.get('source', 'kingdomai')
    if source_name not in ('kingdomai', 'demo'):
        raise ValueError('source must be kingdomai or demo')
    output = Path(runtime['task_output_dir']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    progress = runtime.get('task_progress') or (lambda value: None)
    check_cancel = runtime.get('task_check_cancel') or (lambda: None)
    save_task_checkpoint = runtime.get('task_save_checkpoint') or (lambda value: None)
    check_cancel()
    # Deferred: generic task and Web processes do not depend on the ML stack.
    from src.quant_research.automl.advisor import ResearchAdvisor
    from src.quant_research.automl.planning import compile_research, explicit_plan, normalize_plan
    from src.quant_research.automl.runner import write_json
    from src.quant_research.automl.study import run_study
    fingerprint = hashlib.sha256(json.dumps({'requirement': requirement, 'spec': params.get('spec'), 'source': source_name},
                                          sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    plan_path = output / 'research_design.json'
    if plan_path.exists():
        plan = normalize_plan(json.loads(plan_path.read_text()))
        if plan.get('source') != source_name or plan['requirement_brief'] != requirement or plan.get('input_fingerprint', fingerprint) != fingerprint:
            raise ValueError('task input differs from the frozen research design')
    else:
        progress({'stage': '研究设计', 'message': '根据用户要求设计研究方向与共享实验预算'})
        plan = (explicit_plan(params['spec'], requirement) if params.get('spec') is not None else
                compile_research(requirement, reference_time=runtime.get('scheduled_for')))
        plan.update(source=source_name, input_fingerprint=fingerprint)
        write_json(plan_path, plan)
    progress({'stage': '研究设计', 'message': plan['design']})
    checkpoint_path = output / 'research_checkpoint.json'
    saved = runtime.get('task_checkpoint') or (json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {})
    def save(value):
        write_json(checkpoint_path, value)
        save_task_checkpoint(value)
    _, study = run_study(plan, source_name=source_name, output_root=output,
                         advisor=ResearchAdvisor() if params.get('llm_review', True) else None,
                         progress=progress, check_cancel=check_cancel, checkpoint=save, saved=saved)
    return _result(output, plan_path, plan, study)
