import argparse
import json
from pathlib import Path

from src.skill_runtime import SkillRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a skill alpha entry locally.")
    parser.add_argument("--skill", required=True, help="Skill name, e.g. hotspot_trace")
    parser.add_argument("--input", required=True, help="Path to input payload json")
    parser.add_argument(
        "--tools",
        default="",
        help="Optional debug override. Comma-separated allowed tools.",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Max agent loop steps. Overrides skill default when provided.",
    )
    parser.add_argument(
        "--enable-think",
        action="store_true",
        help="Enable model thinking mode. Default is off.",
    )
    parser.add_argument(
        "--tool-mode",
        choices=["strict", "auto", "free"],
        default="",
        help="Optional tool policy mode override for testing.",
    )
    parser.add_argument(
        "--execution-profile",
        default="real",
        help="Default execution profile for tool calls, e.g. real or mock.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output json path. If omitted, print to stdout.",
    )
    args = parser.parse_args()

    input_payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    runner = SkillRunner()
    skill = runner.load_skill(args.skill)

    if args.tool_mode:
        tool_policy = dict(skill.config.get("tool_policy") or {})
        tool_policy["mode"] = str(args.tool_mode).strip()
        skill.config["tool_policy"] = tool_policy

    selection_detail = None
    selected_tools = [x.strip() for x in str(args.tools or "").split(",") if x.strip()]
    if not selected_tools:
        selection_detail = runner.tool_selector.select_detailed(
            skill_name=skill.name,
            skill_md=skill.skill_md,
            skill_config=skill.config,
            input_payload=input_payload,
        )
        selected_tools = selection_detail.get("selected_tools") or []

    result = runner.run(
        skill_name=args.skill,
        input_payload=input_payload,
        allowed_tools=selected_tools,
        max_steps=max(1, int(args.max_steps)) if args.max_steps is not None else None,
        enable_think=bool(args.enable_think),
        default_execution_profile=str(args.execution_profile or "real").strip() or "real",
    )
    payload = result.to_dict()
    payload["selected_tools"] = selected_tools
    payload["tool_mode"] = str((skill.config.get("tool_policy") or {}).get("mode") or "")
    payload["execution_profile"] = str(args.execution_profile or "real").strip() or "real"
    if selection_detail:
        payload["tool_selection"] = selection_detail

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(out_path))
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
