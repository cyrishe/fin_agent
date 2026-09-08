"""Operator CLI: mint a temporary token without changing .env or logging secrets."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Server .env; actual environment takes precedence.")
    parser.add_argument("--principal", help="Required when multiple FINANCE_API_KEYS_JSON entries exist.")
    parser.add_argument("--ttl-hours", type=float, default=4, help="Validity in hours (default: 4).")
    parser.add_argument("--output", type=Path, required=True, help="New secret JSON file, mode 0600; never overwritten.")
    args = parser.parse_args(argv)
    if not math.isfinite(args.ttl_hours) or not 1 <= args.ttl_hours * 3600 <= 2**31 - 1:
        parser.error("--ttl-hours must yield a positive lifetime of at least one second")
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    from src.finance_api.auth import FinanceApiKeyAuth
    try:
        credential = FinanceApiKeyAuth.from_env().issue_temporary_token(
            args.principal, ttl_seconds=int(args.ttl_hours * 3600))
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(credential, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (ValueError, OSError) as exc:
        # Exception type is safe even when a path is user supplied; no key material.
        parser.error(f"Token creation failed ({type(exc).__name__}); check configuration, principal and output path (must not exist).")
    print(json.dumps({"token_file": str(args.output.resolve()),
                      "principal_id": credential["principal_id"],
                      "expires_at_utc": datetime.fromtimestamp(credential["expires_at"], timezone.utc).isoformat()},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
