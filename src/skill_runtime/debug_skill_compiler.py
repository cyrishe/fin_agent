import argparse
import json

from src.skill_runtime.skill_bundle_compiler import SkillBundleCompiler


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug SkillSpec / ExecutionPlan generation.")
    parser.add_argument("--skill", required=True, help="Skill name")
    parser.add_argument("--mode", choices=["spec", "plan", "bundle"], default="spec")
    parser.add_argument("--tool-mode", choices=["strict", "auto", "free"], default="")
    parser.add_argument("--input", default="", help="Optional input payload json path for execution plan")
    args = parser.parse_args()

    compiler = SkillBundleCompiler()
    if args.mode == "spec":
      payload = compiler.bundle_to_skill_spec(args.skill)
      print(json.dumps(payload, ensure_ascii=False, indent=2))
      return
    if args.mode == "bundle":
      spec = compiler.bundle_to_skill_spec(args.skill)
      payload = compiler.skill_spec_to_bundle(spec)
      print(json.dumps(payload, ensure_ascii=False, indent=2))
      return

    if not args.input:
      raise SystemExit("--input is required when mode=plan")
    with open(args.input, "r", encoding="utf-8") as f:
      input_payload = json.load(f)
    payload = compiler.build_execution_plan(
      skill_name=args.skill,
      input_payload=input_payload,
      tool_mode=args.tool_mode,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
