"""Stage immutable training packages, audit the local mirror, and record them in DB."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = ROOT / "outputs/stock_automl/aliyun_drive_local/策略训练/收盘开盘_20261014"
CLOUD_ROOT = "策略训练/收盘开盘_20261014"
SCHEMA = ROOT / "deploy/sql/automl_training_archive.sql"
CATEGORIES = (
    "01_时间稳定性_七因子基线",
    "02_联合目标与尾部风险",
    "03_因子筛选_7至14",
    "04_次日目标价_收盘均价次高最高",
    "05_六切面序列_卷积网络",
)
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,127}\Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package(directory: Path) -> tuple[str, int]:
    manifest = directory / "SHA256SUMS"
    if not manifest.is_file():
        raise ValueError(f"Missing SHA256SUMS: {directory}")
    seen: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            raise ValueError(f"Invalid SHA256SUMS line: {line!r}")
        expected, name = match.groups()
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or name in seen:
            raise ValueError(f"Invalid package path: {name}")
        seen.add(name)
        file = directory / relative
        if not file.is_file() or file.is_symlink() or sha256_file(file) != expected:
            raise ValueError(f"Package hash mismatch: {name}")
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
    if actual != seen | {"SHA256SUMS"}:
        raise ValueError(f"Package file list differs from SHA256SUMS: {directory}")
    return sha256_file(manifest), len(seen)


def category_name(value: str) -> str:
    if value not in CATEGORIES:
        raise ValueError(f"Unknown category: {value}")
    return value


def prepare_local_root(root: Path) -> None:
    for category in CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)


def stage_package(source: Path, root: Path, category: str, folder_name: str) -> tuple[Path, str, int]:
    category_name(category)
    if not folder_name or Path(folder_name).name != folder_name or folder_name in {".", ".."}:
        raise ValueError("folder_name must be one directory name")
    expected_sha, count = verify_package(source)
    prepare_local_root(root)
    destination = root / category / folder_name
    if destination.exists():
        actual_sha, actual_count = verify_package(destination)
        if (actual_sha, actual_count) != (expected_sha, count):
            raise ValueError(f"Existing archive has different content: {destination}")
        return destination, actual_sha, actual_count
    with tempfile.TemporaryDirectory(prefix=".archive-", dir=destination.parent) as temporary:
        staged = Path(temporary) / folder_name
        shutil.copytree(source, staged)
        actual_sha, actual_count = verify_package(staged)
        if (actual_sha, actual_count) != (expected_sha, count):
            raise ValueError("Copied package failed readback")
        staged.rename(destination)
    return destination, expected_sha, count


def record_local_package(env_file: Path, record: dict) -> None:
    from scripts.run_automl_1440_strategy import write_connection

    with write_connection(env_file) as db:
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    "SELECT manifest_sha256,local_path,cloud_path,source_commit,"
                    "category_path,file_count FROM automl_training_archive "
                    "WHERE user_id=%s AND experiment_id=%s FOR UPDATE",
                    (record["user_id"], record["experiment_id"]),
                )
                existing = cursor.fetchone()
                identity_fields = ("manifest_sha256", "local_path", "cloud_path",
                                   "source_commit", "category_path", "file_count")
                if existing and any(existing[key] != record[key] for key in identity_fields):
                    raise ValueError("experiment_id already belongs to a different package")
                if not existing:
                    cursor.execute(
                        "INSERT INTO automl_training_archive "
                        "(user_id,experiment_id,experiment_date,title,category_path,"
                        "local_path,cloud_path,source_commit,manifest_sha256,file_count,"
                        "local_verified_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(6))",
                        tuple(record[key] for key in (
                            "user_id", "experiment_id", "experiment_date", "title",
                            "category_path", "local_path", "cloud_path", "source_commit",
                            "manifest_sha256", "file_count",
                        )),
                    )
            db.commit()
        except Exception:
            db.rollback()
            raise


def audit_local(root: Path) -> list[dict]:
    packages = []
    for manifest in sorted(root.glob("*/**/SHA256SUMS")):
        package = manifest.parent
        try:
            digest, count = verify_package(package)
            packages.append({"path": str(package), "manifest_sha256": digest,
                             "file_count": count, "valid": True})
        except ValueError as exc:
            packages.append({"path": str(package), "valid": False, "error": str(exc)})
    return packages


def audit_db(env_file: Path, user_id: str, root: Path, packages: list[dict]) -> dict:
    from scripts.run_automl_1440_strategy import write_connection

    with write_connection(env_file) as db:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT experiment_id,local_path,cloud_path,manifest_sha256,"
                "cloud_verified_at FROM automl_training_archive WHERE user_id=%s "
                "ORDER BY experiment_date,experiment_id", (user_id,))
            rows = cursor.fetchall()
    by_path = {item["path"]: item for item in packages}
    checked = []
    registered_paths = set()
    for row in rows:
        local_path = row["local_path"]
        registered_paths.add(local_path)
        package = by_path.get(local_path)
        if package is None:
            local_check = "missing"
        elif not package["valid"]:
            local_check = "invalid"
        elif package["manifest_sha256"] != row["manifest_sha256"]:
            local_check = "hash_mismatch"
        else:
            local_check = "ok"
        checked.append({"experiment_id": row["experiment_id"],
                        "local_path": local_path, "local_check": local_check,
                        "cloud_path": row["cloud_path"],
                        "cloud_verified_at": (row["cloud_verified_at"].isoformat()
                                              if row["cloud_verified_at"] else None)})
    return {"records": checked,
            "unregistered_local_packages": sorted(set(by_path) - registered_paths),
            "local_root": str(root)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=LOCAL_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-local")
    audit = sub.add_parser("audit")
    audit.add_argument("--check-db", action="store_true")
    audit.add_argument("--env-file", type=Path, default=ROOT / ".env")
    audit.add_argument("--user-id", default=getpass.getuser())
    stage = sub.add_parser("stage")
    stage.add_argument("--source", type=Path, required=True)
    stage.add_argument("--category", required=True)
    stage.add_argument("--folder-name", required=True)
    stage.add_argument("--experiment-id", required=True)
    stage.add_argument("--experiment-date", type=date.fromisoformat, required=True)
    stage.add_argument("--title", required=True)
    stage.add_argument("--source-commit", required=True)
    stage.add_argument("--env-file", type=Path, default=ROOT / ".env")
    stage.add_argument("--user-id", default=getpass.getuser())
    stage.add_argument("--write-db", action="store_true")
    args = parser.parse_args()
    if args.command == "init-local":
        prepare_local_root(args.root)
        print(json.dumps({"local_root": str(args.root), "categories": CATEGORIES}, ensure_ascii=False))
    elif args.command == "audit":
        handoff_dir = ROOT / "docs/stock_automl_runs"
        handoffs = sorted(handoff_dir.glob("HANDOFF_????????.md"))
        latest = handoffs[-1] if handoffs else None
        related = sorted(handoff_dir.glob(f"{latest.stem}_*.md")) if latest else []
        packages = audit_local(args.root)
        result = {"branch": subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
            "read_first": [str(ROOT / "docs/stock_automl_runs/ACTIVE.md"),
                           str(latest) if latest else None,
                           *[str(path) for path in related],
                           str(ROOT / "docs/stock_automl_runs/EXPERIMENT_REGISTER.md")],
            "local_root": str(args.root), "packages": packages}
        if args.check_db:
            result["database"] = audit_db(args.env_file, args.user_id, args.root, packages)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if not ID_RE.fullmatch(args.experiment_id):
            raise ValueError("experiment_id must be a stable ASCII identifier")
        if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
            raise ValueError("source_commit must be a full Git SHA")
        destination, digest, count = stage_package(
            args.source, args.root, args.category, args.folder_name)
        cloud_path = "/".join((CLOUD_ROOT, args.category, args.folder_name))
        record = {"user_id": args.user_id, "experiment_id": args.experiment_id,
                  "experiment_date": args.experiment_date.isoformat(), "title": args.title,
                  "category_path": args.category, "local_path": str(destination),
                  "cloud_path": cloud_path, "source_commit": args.source_commit,
                  "manifest_sha256": digest, "file_count": count}
        if args.write_db:
            record_local_package(args.env_file, record)
        print(json.dumps({**record, "db_recorded": args.write_db,
                          "cloud_verified": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
