import argparse
import json

from src.skill_runtime.intent_router import IntentRouter


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug natural-language intent routing into skill routes.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--context", default="{}")
    args = parser.parse_args()

    context = json.loads(args.context or "{}")
    router = IntentRouter()
    route = router.route(user_text=args.text, context=context)
    composite = router.build_composite_execution_plan(route=route)
    payload = {
        "route": route,
        "composite_execution_plan": composite,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
