import json

from src.tools.stock_funds_tool import run


def main():
    payload = run(
        {
            "code": "贵州茅台",
            "n_days": 30,
            "include_flow_news": True,
            "persist": True,
            "dedupe": True,
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
