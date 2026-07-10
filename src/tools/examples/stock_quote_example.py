import json

from src.tools.stock_quote_tool import run


def main():
    payload = run(
        {
            "code": "600519",
            "history_days": 60,
            "minute_count": 60,
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
