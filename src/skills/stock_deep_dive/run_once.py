import argparse
import json
from pathlib import Path

from src.skill_runtime import SkillRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run stock_deep_dive once without going through Flask/API.")
    parser.add_argument("--code", required=True, help="Stock code, e.g. 600519")
    parser.add_argument("--name", default="", help="Stock name, e.g. 贵州茅台")
    parser.add_argument("--question", default="", help="Optional custom question.")
    parser.add_argument("--focus", default="全面分析", help="Optional context focus.")
    parser.add_argument("--as-of-date", default="", help="Optional context date, e.g. 2026-03-17")
    parser.add_argument("--max-steps", type=int, default=6, help="Max agent steps.")
    parser.add_argument("--enable-think", action="store_true", help="Enable model thinking.")
    parser.add_argument("--output", default="", help="Optional output file path.")
    args = parser.parse_args()

    code = str(args.code or "").strip()
    name = str(args.name or "").strip()
    question = str(args.question or "").strip() or f"请从行情、资金、研报、新闻和风险角度，对{(name or code)}做一份专业、克制的投顾式分析。"
    as_of_date = str(args.as_of_date or "").strip()

    input_payload = {
        "task_type": "stock_deep_dive",
        "code": code,
        "name": name,
        "runtime_mode": "interactive",
        "question": question,
        "context": {
            "focus": str(args.focus or "").strip() or "全面分析",
            "as_of_date": as_of_date,
        },
    }
    if not as_of_date:
        input_payload["context"].pop("as_of_date", None)

    runner = SkillRunner()
    result = runner.run(
        skill_name="stock_deep_dive",
        input_payload=input_payload,
        max_steps=max(1, int(args.max_steps)),
        enable_think=bool(args.enable_think),
    )
    payload = result.to_dict()

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(out_path))
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
