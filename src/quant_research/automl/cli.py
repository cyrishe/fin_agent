"""Independent stock AutoML command-line entrypoint."""
from pathlib import Path
import argparse
import json

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description="Stock AutoML research; SELECT-only database access")
    parser.add_argument("--config", type=Path, help="ResearchSpec JSON; explicit fields are authoritative")
    parser.add_argument("--demo", action="store_true", help="synthetic market; no database access")
    parser.add_argument("--inventory", action="store_true", help="read schema catalog and save locally, no training")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--objective", help="candidate exploration guidance; executable constraints come from --config or --requirement")
    parser.add_argument("--requirement", help="compile complete natural-language constraints through the configured LLM")
    parser.add_argument("--resume", type=Path, help="resume a frozen run directory without reloading market data")
    parser.add_argument("--review-only", type=Path, help="retry the LLM assessment of saved evidence without retraining")
    parser.add_argument("--max-trials", type=int)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--minute", action="store_true")
    parser.add_argument("--llm", action="store_true", help="plan and review through existing LLM env configuration")
    parser.add_argument("--db-env", help="name of env variable holding a kingdomai DB URL")
    parser.add_argument("--events-csv", action="append", default=[], help="local PIT features: symbol,available_at,numeric fields")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/stock_automl")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from src.quant_research.automl.config import ResearchSpec
    from src.quant_research.automl.data import inventory, kingdom_connection, load_kingdom
    from src.quant_research.automl.runner import run_research, review_research, write_json
    from src.quant_research.automl.study import run_study, load_market
    from src.quant_research.automl.assets import export_strategy
    from src.quant_research.automl.planning import compile_research, explicit_plan, normalize_plan
    from src.quant_research.automl.advisor import ResearchAdvisor
    import pandas as pd
    loader_options = {"db_env": args.db_env, "events_csv": [str(Path(p).resolve()) for p in args.events_csv]}
    def loader(spec, source_name):
        if source_name == "demo":
            daily, events, source = load_market(spec, source_name)
        else:
            daily, events, source = load_kingdom(spec, env_name=loader_options["db_env"])
            if spec.include_minute and not any("minute_bars" in event for event in events):
                raise ValueError("明确要求的分钟特征不可用")
        events = list(events)
        for path in loader_options["events_csv"]:
            events.append(pd.read_csv(path, dtype={"symbol": str}))
        return daily, events, source
    if args.review_only:
        print(json.dumps(review_research(args.review_only, ResearchAdvisor()), ensure_ascii=False))
        return
    if args.resume:
        if (args.resume / "research_design.json").exists():
            plan = json.loads((args.resume / "research_design.json").read_text())
            loader_options = plan.get("loader_options", loader_options)
            checkpoint_path = args.resume / "research_checkpoint.json"
            _, study = run_study(plan, source_name=plan["source"], output_root=args.resume, market_loader=loader,
                                 advisor=ResearchAdvisor() if args.llm else None,
                                 saved=json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else None,
                                 checkpoint=lambda value: write_json(checkpoint_path, value))
            print(json.dumps({"output": str(args.resume), "strategies": len(study["strategies"])}, ensure_ascii=False))
            return
        spec = ResearchSpec.from_dict(json.loads((args.resume / "spec.json").read_text()))
        _, report = run_research(spec, resume_dir=args.resume, advisor=ResearchAdvisor() if args.llm else None)
        export_strategy(args.resume, hypothesis=spec.objective)
        print(json.dumps({"run_id": report["run_id"], "successful_trials": report["successful_trials"]}, ensure_ascii=False))
        return
    if args.inventory:
        with kingdom_connection(args.db_env) as conn:
            catalog = inventory(conn)
        args.output.mkdir(parents=True, exist_ok=True)
        write_json(args.output / "catalog.json", catalog)
        print(f"{len(catalog)} tables: {args.output / 'catalog.json'}")
        return
    if args.requirement and args.config:
        parser.error("choose --requirement or --config, not both")
    if args.requirement:
        plan = compile_research(args.requirement)
        print(plan["design"])
    else:
        value = json.loads(args.config.read_text()) if args.config else {"start": args.start, "end": args.end}
        plan = explicit_plan(value, args.objective or "按显式配置进行股票策略研究")
    overrides = {key: getattr(args, key) for key in ("objective", "max_trials", "max_symbols", "seed")
                 if getattr(args, key) is not None}
    if args.minute:
        overrides["include_minute"] = True
    plan["spec"].update(overrides)
    for direction in plan["directions"]:
        direction["spec"].update(overrides)
    plan = normalize_plan(plan)
    plan["source"] = "demo" if args.demo else "kingdomai"
    plan["loader_options"] = loader_options
    from uuid import uuid4
    output = args.output / ("study_" + uuid4().hex[:12])
    output.mkdir(parents=True)
    write_json(output / "research_design.json", plan)
    _, study = run_study(plan, source_name=plan["source"], output_root=output, market_loader=loader,
                         checkpoint=lambda value: write_json(output / "research_checkpoint.json", value),
                         advisor=ResearchAdvisor() if args.llm else None)
    print(json.dumps({"output": str(output), "strategies": len(study["strategies"]),
                      "saved_models": sum(bool(s.get("report")) for s in study["strategies"])}, ensure_ascii=False))
