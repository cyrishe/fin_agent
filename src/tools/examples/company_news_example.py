import json

from src.tools.company_news_tool import run


def main():
    payload = run(
        {
            "query": "贵州茅台",
            "entity_type": "auto",
            "max_results_per_site": 5,
            "keep_days": 3,
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
