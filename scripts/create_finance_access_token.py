"""Operator CLI: create a revocable Finance API token and write it once under /tmp."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _reserve_output(path: Path | None) -> tuple[int, Path]:
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if path is None:
        fd, generated = tempfile.mkstemp(
            prefix="fin-agent-access-token-", suffix=".json", dir=temporary_root,
        )
        os.fchmod(fd, 0o600)
        return fd, Path(generated)

    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(temporary_root)
    except ValueError as exc:
        raise ValueError(f"--output must be inside the temporary directory {temporary_root}") from exc
    fd = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return fd, resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Server .env; actual environment takes precedence.")
    parser.add_argument("--project", help="Optional project label for later management.")
    parser.add_argument("--name", help="Optional human-readable label for later management.")
    parser.add_argument("--principal", help="Required when multiple FINANCE_API_KEYS_JSON entries exist.")
    lifetime = parser.add_mutually_exclusive_group()
    lifetime.add_argument("--ttl-hours", type=float, help="Validity in hours (default: 4).")
    lifetime.add_argument(
        "--never-expires",
        action="store_true",
        help="Explicitly create a token without automatic expiry; it remains revocable.",
    )
    parser.add_argument("--created-by", help="Optional operator identifier for the audit record.")
    parser.add_argument(
        "--output",
        type=Path,
        help="New JSON file below the system temporary directory; defaults to a unique /tmp file.",
    )
    args = parser.parse_args(argv)
    ttl_hours = 4 if args.ttl_hours is None else args.ttl_hours
    if not args.never_expires and (
        not math.isfinite(ttl_hours) or not 1 <= ttl_hours * 3600 <= 2**31 - 1
    ):
        parser.error("--ttl-hours must yield a positive lifetime of at least one second")
    if args.env_file:
        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)

    fd: int | None = None
    output: Path | None = None
    try:
        fd, output = _reserve_output(args.output)
        from src.finance_api.auth import FinanceApiKeyAuth
        credential = FinanceApiKeyAuth.from_env().issue_managed_token(
            project_name=args.project,
            token_name=args.name,
            principal_id=args.principal,
            ttl_seconds=None if args.never_expires else int(ttl_hours * 3600),
            created_by=args.created_by,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(credential, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (ValueError, OSError) as exc:
        if fd is not None:
            os.close(fd)
        if output is not None:
            try:
                output.unlink()
            except FileNotFoundError:
                pass
        parser.error(
            f"Token creation failed ({type(exc).__name__}); check configuration, database, principal and output path."
        )

    expires_at = credential["expires_at"]
    summary = {
        "token_file": str(output),
        "token_id": credential["token_id"],
        "project_name": credential["project_name"],
        "token_name": credential["token_name"],
        "principal_id": credential["principal_id"],
        "masked_token": credential["masked_token"],
        "never_expires": credential["never_expires"],
        "expires_at_utc": None if expires_at is None else datetime.fromtimestamp(
            expires_at, timezone.utc
        ).isoformat(),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
