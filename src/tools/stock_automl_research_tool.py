"""Background-only adapter. The independent research engine knows nothing about tasks."""
from __future__ import annotations

import json
from pathlib import Path


def run(args, *, runtime_ctx=None):
    runtime = dict(runtime_ctx or {})
    if not runtime.get("task_run_id") or not runtime.get("owner_user_id") or not runtime.get("task_output_dir"):
        raise ValueError("stock_automl_research must run through an authorized background task")
    params = dict(args)
    params.pop("_runtime", None)
    unknown = set(params) - {"requirement_brief", "spec", "source", "llm_review"}
    if unknown:
        raise ValueError("unsupported tool arguments: " + ", ".join(sorted(unknown)))
    requirement = params.get("requirement_brief", "")
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError("requirement_brief is required")
    source_name = params.get("source", "kingdomai")
    if source_name not in ("kingdomai", "demo"):
        raise ValueError("source must be kingdomai or demo")
    output = Path(runtime["task_output_dir"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    progress = runtime.get("task_progress") or (lambda value: None)
    check_cancel = runtime.get("task_check_cancel") or (lambda: None)
    save_task_checkpoint = runtime.get("task_save_checkpoint") or (lambda value: None)
    check_cancel()

    # Imports are deliberately deferred: the Web service need not install ML dependencies.
    from src.quant_research.automl.advisor import ResearchAdvisor
    from src.quant_research.automl.config import ResearchSpec
    from src.quant_research.automl.planning import compile_research, explicit_plan
    from src.quant_research.automl.runner import model_display_name, run_research, write_json
    plan_path = output / "research_design.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
    else:
        progress({"stage": "研究设计", "message": "将用户要求落实到可执行研究参数"})
        if params.get("spec") is not None:
            plan = explicit_plan(params["spec"], requirement)
        else:
            plan = compile_research(requirement, reference_time=runtime.get("scheduled_for"))
        plan["source"] = source_name
        write_json(plan_path, plan)
    if plan.get("source") != source_name or plan["requirement_brief"] != requirement:
        raise ValueError("task input differs from the frozen research design")
    spec = ResearchSpec.from_dict(plan["spec"])
    progress({"stage": "研究设计", "message": plan["design"]})
    check_cancel()
    checkpoint_path = output / "research_checkpoint.json"
    saved = runtime.get("task_checkpoint") or (json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {})
    resume_dir = saved.get("research_dir")
    if resume_dir:
        resume_dir = Path(resume_dir)
        resume_dir = (resume_dir if resume_dir.is_absolute() else output / resume_dir).resolve()
        if not resume_dir.is_relative_to(output):
            raise ValueError("research checkpoint is outside the authorized task directory")
    def save(value):
        # Relative references survive the runtime copying a prior claim into an isolated attempt.
        value = {**value, "research_dir": str(Path(value["research_dir"]).resolve().relative_to(output))}
        write_json(checkpoint_path, value)
        save_task_checkpoint(value)
    daily, events, source = None, (), None
    if not resume_dir:
        progress({"stage": "准备数据", "message": "读取研究股票池与历史可用特征"})
        if source_name == "demo":
            import pandas as pd
            from src.quant_research.automl.demo import synthetic_market
            sessions = len(pd.bdate_range(spec.start, spec.end))
            if not 140 <= sessions <= 1200:
                raise ValueError("demo requires 140..1200 synthetic business sessions")
            if spec.symbols or spec.industries or spec.include_minute:
                raise ValueError("demo uses synthetic companies/industries and daily data; real symbols, industry filters and minute requirements need kingdomai")
            daily, events = synthetic_market(seed=spec.seed, companies=spec.max_symbols, sessions=sessions, start=spec.start)
            source = {"source": "synthetic_demo", "warnings": ["合成数据仅验证程序，不用于股票效果或投资判断。"]}
        else:
            from src.quant_research.automl.data import load_kingdom
            daily, events, source = load_kingdom(spec)
            if spec.include_minute and not any("minute_bars" in event for event in events):
                raise ValueError("明确要求的分钟特征在当前数据库/时间范围内不可用，请调整数据范围或研究要求。")
    advisor = ResearchAdvisor() if params.get("llm_review", True) else None
    root, report = run_research(spec, daily, events, source=source, output_root=output,
                               resume_dir=resume_dir, advisor=advisor, progress=progress,
                               check_cancel=check_cancel, checkpoint=save)
    report = json.loads((root / "report.json").read_text())
    # Published evidence is aggregate; panel, executable model, raw predictions and credentials remain private.
    names = ["report.md", "report.json", "review.md", "review_status.json", "review_evidence.json", "sample_counts.json",
             "development.json", "selection.json", "spec.json"]
    artifacts = [{"name": "研究设计", "path": str(plan_path), "mime_type": "application/json"}]
    artifacts += [{"name": name, "path": str(root / name),
                   "mime_type": "text/markdown" if name.endswith(".md") else "application/json"} for name in names]
    qualified = report["development_constraints_met"]
    selected = report["selected"]
    summary = (f"本轮最佳候选为{model_display_name(selected)}，持有 {selected['horizon']} 个交易日。"
               f"完成 {report['attempted_trials']} 个候选实验，其中 {report['successful_trials']} 个可评估。"
               + ("开发集选出的模型满足预设筛选条件；泛化仍以留出结果为准。" if qualified else "本轮未找到满足全部开发集条件的模型，已保存最佳候选及未达标证据。"))
    if source_name == "demo":
        summary = "合成数据流程验证。" + summary
    counts = report["sample_counts"]
    metrics = [{"label": "候选模型", "value": model_display_name(selected)},
               {"label": "持有周期", "value": selected["horizon"], "unit": "交易日"},
               {"label": "行情样本行数", "value": counts["panel_rows"]},
               {"label": "股票数", "value": counts["companies"]},
               {"label": "开发集行数", "value": counts["development"]["input_rows"]},
               {"label": "实际拟合行数", "value": counts["development"]["fit_rows"]},
               {"label": "概率校准行数", "value": counts["development"]["calibration_rows"]},
               {"label": "成功实验数", "value": report["successful_trials"]}]
    for name, evidence in report["evaluation"].items():
        if "prediction" not in evidence:
            continue
        label = "原公司新时段" if name == "new_period" else "新公司新时段"
        win_rate = evidence["prediction"]["signal_win_rate_after_cost"]
        metrics += [{"label": label + "样本行数", "value": evidence["prediction"]["rows"]},
                    {"label": label + "信号数", "value": evidence["prediction"]["signal_count"]},
                    {"label": label + "扣成本信号胜率", "value": round(win_rate * 100, 2) if win_rate is not None else "无信号", "unit": "%" if win_rate is not None else ""},
                    {"label": label + "组合收益", "value": round(evidence["portfolio"]["total_return"] * 100, 3), "unit": "%"},
                    {"label": label + "组合最大回撤", "value": round(evidence["portfolio"]["max_drawdown"] * 100, 3), "unit": "%"}]
    return {"tool": "stock_automl_research", "ok": True, "summary": summary,
            "report_markdown": (root / "report.md").read_text(), "metrics": metrics, "artifacts": artifacts,
            "domain_result": {"design": plan["design"], "spec": spec.to_dict(),
                              **{key: report[key] for key in ("sample_counts", "selected", "development_constraints_met", "evaluation", "limitations")},
                              "review_status": json.loads((root / "review_status.json").read_text())}}
