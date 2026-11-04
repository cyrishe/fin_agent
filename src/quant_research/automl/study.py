"""Run a user research brief as independent strategies under one trial budget.

The task adapter owns authorization and lifecycle. This module owns only the
research directions, their frozen execution and the resulting local assets.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from .config import ResearchSpec
from .planning import normalize_plan
from .runner import run_research, write_json, model_display_name


def load_market(spec, source_name):
    if source_name == "demo":
        import pandas as pd
        from .demo import synthetic_market
        sessions = len(pd.bdate_range(spec.start, spec.end))
        if not 140 <= sessions <= 1200:
            raise ValueError("demo requires 140..1200 synthetic business sessions")
        if spec.symbols or spec.industries or spec.include_minute:
            raise ValueError("demo uses synthetic companies/industries and daily data; real symbols, industry filters and minute requirements need kingdomai")
        daily, events = synthetic_market(seed=spec.seed, companies=spec.max_symbols or 8, sessions=sessions, start=spec.start)
        daily.attrs["market_calendar"] = sorted(daily.date.unique())
        return daily, events, {"source": "synthetic_demo", "warnings": ["合成数据仅验证程序，不用于股票效果或投资判断。"]}
    from .data import load_kingdom
    daily, events, source = load_kingdom(spec)
    if spec.include_minute and not any("minute_bars" in event for event in events):
        raise ValueError("明确要求的分钟特征在当前数据库/时间范围内不可用，请调整数据范围或研究要求。")
    return daily, events, source


def allocated_directions(plan):
    """One task budget, divided reproducibly; no direction gets a fresh full budget."""
    directions = plan["directions"]
    base, remainder = divmod(plan["spec"]["max_trials"], len(directions))
    return [(item, replace(ResearchSpec.from_dict(item["spec"]), max_trials=base + (i < remainder)))
            for i, item in enumerate(directions)]


def _inside(root, reference):
    path = (root / reference).resolve()
    if not path.is_relative_to(root):
        raise ValueError("research checkpoint is outside the authorized task directory")
    return path


def render_study(study):
    lines = ["# 用户策略研究", "",
             f"研究 {len(study['strategies'])} 个方向，共用 {study['trial_budget']} 个候选实验预算。各方向独立交付，不根据最终测试挑选统一赢家。", "",
             "| 研究方向 | 模型与持有期 | 开发筛选 | 后续时间精确率 | 选中 / 出手日期 |",
             "|---|---|---|---|---|"]
    for index, strategy in enumerate(study["strategies"], 1):
        hypothesis = f"方向 {index}"
        report = strategy.get("report")
        if report is None:
            lines.append(f"| {hypothesis} | 未产出模型 | 未完成 | — | — |")
            continue
        c = report["selected"]
        m = report["evaluation"].get("new_period", {}).get("prediction", {})
        precision = m.get("target_precision")
        precision_text = f"{precision:.2%}" if precision is not None else "无信号或不可评估"
        verdict = "满足开发条件，查看测试证据" if report["development_constraints_met"] else "未满足开发条件"
        lines.append(f"| {hypothesis} | {model_display_name(c)} / {c['horizon']}日 | {verdict} | {precision_text} | {m.get('signal_count', 0)} / {m.get('signal_dates', 0)} |")
    lines += ["", "精确率是选中事件达到目标的比例，不等于扣成本收益或未来保证。未达标的候选仍保留研究证据，不作为已验证有效策略。", ""]
    for index, strategy in enumerate(study["strategies"], 1):
        lines += [f"## 方向 {index}", "", strategy["hypothesis"], ""]
        if strategy.get("error"):
            lines += ["执行说明：" + strategy["error"], ""]
        else:
            counts = strategy["report"]["sample_counts"]
            training = counts["development"]
            lines += [f"数据覆盖 {counts['companies']} 家公司、{counts['sessions']} 个交易日、{counts['panel_rows']:,} 条行情。"
                      f"最终模型拟合 {training['fit_rows']:,} 条，概率校准 {training['calibration_rows']:,} 条；"
                      f"后续时间留出从 {str(counts['test_start'])[:10]} 开始。", ""]
            if counts.get("policy_selection"):
                policy = counts["policy_selection"]
                lines += [f"另用开发末段 {policy['rows']:,} 条样本选择并冻结筛选门槛；这部分不参与最终模型拟合或校准。", ""]
            lines += [f"该方向保存独立的研究报告、策略定义、模型解释及样本/验证证据。策略标识：`{strategy['strategy_id']}`。", ""]
    return "\n".join(lines)


def run_study(plan, *, source_name, output_root, advisor=None, progress=lambda _: None,
              check_cancel=lambda: None, checkpoint=lambda _: None, saved=None, market_loader=None):
    plan = normalize_plan(plan)
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Completed directions are skipped below, so validate the entire study plan
    # before that shortcut can mix old assets with changed execution semantics.
    frozen = {"plan": json.loads(json.dumps(plan)), "source": source_name}
    plan_path = root / "study_plan.json"
    if plan_path.exists():
        if json.loads(plan_path.read_text()) != frozen:
            raise ValueError("study plan differs from frozen research plan")
    else:
        write_json(plan_path, frozen)
    saved = dict(saved or {})
    references = dict(saved.get("research_dirs", {}))
    if saved.get("research_dir"):
        references.setdefault("direction_1", saved["research_dir"])
    for reference in references.values():
        _inside(root, reference)
    study_path = root / "study.json"
    study = json.loads(study_path.read_text()) if study_path.exists() else {
        "requirement_brief": plan["requirement_brief"], "design": plan["design"],
        "trial_budget": plan["spec"]["max_trials"], "source": source_name, "strategies": []}
    if study["requirement_brief"] != plan["requirement_brief"] or study["source"] != source_name:
        raise ValueError("study differs from frozen research requirement")
    completed = {s["id"]: s for s in study["strategies"]}
    for existing in completed.values():
        if existing.get("research_dir"):
            _inside(root, existing["research_dir"])
    loader = market_loader or load_market
    cache = {}
    allocation = allocated_directions(plan)
    for index, (direction, spec) in enumerate(allocation):
        check_cancel()
        direction_id = direction["id"]
        if direction_id in completed:
            continue
        used_before = sum(item.get("attempted_trials", item.get("report", {}).get("attempted_trials", 0)) for item in study["strategies"])
        prefix = f"方向 {index + 1}/{len(allocation)}"
        def emit(event):
            update = {**event, "direction_id": direction_id, "direction_index": index + 1,
                      "direction_count": len(allocation), "hypothesis": direction["hypothesis"],
                      "message": f"{prefix} · {event.get('message', '')}"}
            if len(allocation) > 1 and event.get("stage") == "完成":
                update["stage"] = "方向结果已保存"
            if len(allocation) > 1 and "completed" in update:
                update.update(completed=used_before + int(update["completed"]), total=plan["spec"]["max_trials"])
            progress(update)
        def save(value):
            references[direction_id] = str(Path(value["research_dir"]).resolve().relative_to(root))
            checkpoint({"research_dirs": references.copy(), "completed_directions": list(completed),
                        "current_direction": direction_id})
        report, research_root = None, None
        entry = {"id": direction_id, "hypothesis": direction["hypothesis"], "trial_budget": spec.max_trials}
        try:
            reference = references.get(direction_id)
            daily, events, source = None, (), None
            if reference is None:
                emit({"stage": "准备数据", "message": "读取本研究方向的数据范围与历史可用特征"})
                # Reuse only identical data requirements. Strategy features/targets
                # do not affect data loading; each engine still freezes its panel.
                key = json.dumps({k: spec.to_dict()[k] for k in (
                    "start", "end", "symbols", "max_symbols", "industries", "include_minute", "seed")}, sort_keys=True)
                if key not in cache:
                    cache.clear()  # bounded to one market panel
                    cache[key] = loader(spec, source_name)
                daily, events, source = cache[key]
            research_root, report = run_research(spec, daily, events, source=source, output_root=root / direction_id,
                resume_dir=_inside(root, reference) if reference else None, advisor=advisor,
                progress=emit, check_cancel=check_cancel, checkpoint=save)
            from .assets import export_strategy
            asset = export_strategy(research_root, hypothesis=direction["hypothesis"])
            entry.update(research_dir=str(research_root.relative_to(root)), strategy_id=asset["strategy_id"],
                         attempted_trials=report["attempted_trials"], report=report)
        except ValueError as exc:
            entry["error"] = str(exc)[:500]
            entry["attempted_trials"] = 0
            if direction_id in references:
                entry["research_dir"] = references[direction_id]
                state_path = _inside(root, references[direction_id]) / "checkpoint.json"
                if state_path.exists():
                    entry["attempted_trials"] = len(json.loads(state_path.read_text()).get("attempted", []))
        study["strategies"].append(entry)
        completed[direction_id] = entry
        write_json(study_path, study)
        (root / "study.md").write_text(render_study(study))
        checkpoint({"research_dirs": references.copy(), "completed_directions": list(completed)})
    if not any(item.get("report") for item in study["strategies"]):
        reasons = "；".join(f"{item['id']}：{item.get('error', '未产出模型')}" for item in study["strategies"])
        raise ValueError("所有研究方向执行失败，已保留执行证据：" + reasons)
    progress({"stage": "完成", "message": f"已交付 {len(study['strategies'])} 个研究方向的结果与执行证据"})
    return root, study
