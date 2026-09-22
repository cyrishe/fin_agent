"""List masked Finance API tokens or disable one by token ID."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Server .env; actual environment takes precedence.")
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list", help="List token metadata; secrets and digests are omitted.")
    list_parser.add_argument("--project")
    list_parser.add_argument("--limit", type=int, default=100)
    disable_parser = commands.add_parser("disable", help="Permanently disable a managed token.")
    disable_parser.add_argument("--token-id", required=True)
    disable_parser.add_argument("--reason")
    disable_parser.add_argument("--disabled-by")
    args = parser.parse_args(argv)
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)

    from src.finance_api.token_store import FinanceAccessTokenStore, ManagedTokenStoreUnavailable
    store = FinanceAccessTokenStore()
    try:
        if args.command == "list":
            result = {"tokens": store.list_tokens(project_name=args.project, limit=args.limit)}
        else:
            result = {
                "token_id": args.token_id,
                "disabled": store.disable(
                    args.token_id,
                    disabled_by=args.disabled_by,
                    reason=args.reason,
                ),
            }
    except (ValueError, ManagedTokenStoreUnavailable) as exc:
        parser.error(f"Token management failed ({type(exc).__name__}); check input and database configuration.")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
