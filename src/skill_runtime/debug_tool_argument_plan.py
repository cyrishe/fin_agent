import argparse
import json

from src.skill_runtime.tool_argument_planner import ToolArgumentPlanner


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug tool argument planning from natural-language input.")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--text", default="")
    parser.add_argument("--context", default="{}")
    args = parser.parse_args()

    planner = ToolArgumentPlanner()
    context = json.loads(args.context or "{}")
    payload = planner.build_plan(
        tool_name=args.tool,
        user_text=args.text,
        context=context,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
