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
    from src.quant_research.automl.advisor import ResearchAdvisor
    import pandas as pd
    if args.review_only:
        print(json.dumps(review_research(args.review_only, ResearchAdvisor()), ensure_ascii=False))
        return
    if args.resume:
        spec = ResearchSpec.from_dict(json.loads((args.resume / "spec.json").read_text()))
        _, report = run_research(spec, resume_dir=args.resume, advisor=ResearchAdvisor() if args.llm else None)
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
        from src.quant_research.automl.planning import compile_research
        plan = compile_research(args.requirement)
        value = plan["spec"]
        print(plan["design"])
    else:
        value = json.loads(args.config.read_text()) if args.config else {"start": args.start, "end": args.end}
    for key in ("objective", "max_trials", "max_symbols", "seed"):
        if getattr(args, key) is not None:
            value[key] = getattr(args, key)
    if args.minute:
        value["include_minute"] = True
    spec = ResearchSpec.from_dict(value)
    if args.demo:
        from src.quant_research.automl.demo import synthetic_market
        daily, events = synthetic_market(seed=spec.seed)
        source = {"source": "synthetic_demo", "warnings": ["合成数据仅验证程序，不用于股票效果或投资判断。"]}
        spec = ResearchSpec.from_dict({**spec.to_dict(), "start": str(daily.date.min().date()), "end": str(daily.date.max().date())})
    else:
        daily, events, source = load_kingdom(spec, env_name=args.db_env)
    for path in args.events_csv:
        events.append(pd.read_csv(path, dtype={"symbol": str}))
    _, report = run_research(spec, daily, events, source=source, output_root=args.output,
                             advisor=ResearchAdvisor() if args.llm else None)
    print(json.dumps({"run_id": report["run_id"], "successful_trials": report["successful_trials"],
                      "development_constraints_met": report["development_constraints_met"]}, ensure_ascii=False))
