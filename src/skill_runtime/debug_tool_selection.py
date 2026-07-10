import argparse
import json
from pathlib import Path

from src.skill_runtime import SkillRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug tool selection for a skill.")
    parser.add_argument("--skill", required=True, help="Skill name, e.g. hotspot_trace")
    parser.add_argument("--input", required=True, help="Path to input payload json")
    parser.add_argument(
        "--tool-mode",
        choices=["strict", "auto", "free"],
        default="",
        help="Optional tool policy mode override for testing.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output path. If omitted, print to stdout.",
    )
    args = parser.parse_args()

    input_payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    runner = SkillRunner()
    skill = runner.load_skill(args.skill)

    if args.tool_mode:
        tool_policy = dict(skill.config.get("tool_policy") or {})
        tool_policy["mode"] = str(args.tool_mode).strip()
        skill.config["tool_policy"] = tool_policy

    selection_detail = runner.tool_selector.select_detailed(
        skill_name=skill.name,
        skill_md=skill.skill_md,
        skill_config=skill.config,
        input_payload=input_payload,
    )

    payload = {
        "skill_name": skill.name,
        "tool_mode": str((skill.config.get("tool_policy") or {}).get("mode") or ""),
        "declared_tools": [str(x).strip() for x in skill.config.get("tools", []) if str(x).strip()],
        "selected_tools": selection_detail.get("selected_tools") or [],
        "selection_reason": selection_detail.get("selection_reason") or "",
        "discovered_candidates": selection_detail.get("discovered_candidates") or [],
        "input_payload": input_payload,
    }

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(out_path))
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
