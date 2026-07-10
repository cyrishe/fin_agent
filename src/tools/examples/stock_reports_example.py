import json

from src.tools.stock_reports_tool import run


def main():
    payload = run(
        {
            "code": "贵州茅台",
            "limit": 10,
            "refresh": True,
            "persist": True,
            "dedupe": True,
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
