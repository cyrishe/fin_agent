"""Assemble the external reference tools and checksums once, after verification.

Does not change historical source, Git refs, the working index, or any runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_tools(repo: Path, baseline: Path) -> None:
    if (baseline / "SHA256SUMS").exists():
        raise FileExistsError("Reference is already sealed; create a new reference instead of overwriting it")
    for relative in (
        "scripts/build_cc_reference_archive.py", "scripts/run_cc_reference.py",
        "scripts/freeze_cc_reference_history.py", "scripts/seal_cc_reference.py",
        "tests/test_cc_reference_archive.py", "tests/test_cc_reference_runner.py",
        "tests/test_cc_reference_history.py",
    ):
        category = "tests" if relative.startswith("tests/") else "tools"
        target = baseline / category / Path(relative).name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (repo / relative).read_bytes()
        # These are generated copies in the unsealed assembly directory. Once
        # SHA256SUMS exists, the guard above disallows all refreshes.
        target.write_bytes(content)


def seal(repo: Path, baseline: Path, wheelhouse: Path) -> None:
    from build_cc_reference_archive import verify_archive
    prepare_tools(repo, baseline)
    verify_archive(baseline)
    from freeze_cc_reference_history import sanitize
    evidence = baseline / "verification"
    evidence.mkdir(exist_ok=True)
    smoke = repo / "outputs/cc_reference_rebuilt_smoke4_verified_20260908"
    sources = {
        "smoke-metadata.json": smoke / "metadata.json",
        "smoke-results.json": smoke / "results.jsonl",
        "cc-runtime-records.json": smoke / "workspace/outputs/financial_qa_cc/events.jsonl",
        "offline-check.json": repo / "outputs/cc_reference_sealed_entry_check_20260908/metadata.json",
    }
    source_audit = {}
    for filename, source in sources.items():
        raw = source.read_bytes()
        content = ([json.loads(line) for line in raw.decode().splitlines() if line.strip()]
                   if source.suffix == ".jsonl" else json.loads(raw))
        redactions = []
        safe = sanitize(content, redactions=redactions)
        (evidence / filename).write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        source_audit[filename] = {"source": str(source.relative_to(repo)), "sha256": digest(source),
                                  "redactions": redactions}
    (evidence / "source-audit.json").write_text(json.dumps(source_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Exact downloaded wheel bytes are retained locally outside Git. The hash
    # inventory travels with the reference for repeat installations on this OS.
    from packaging.utils import canonicalize_name, parse_wheel_filename
    wheels = sorted(wheelhouse.glob("*.whl"))
    wheel_index = {}
    for wheel in wheels:
        name, version, _, _ = parse_wheel_filename(wheel.name)
        wheel_index[(canonicalize_name(name), str(version))] = wheel
    requirements = []
    for line in (baseline / "requirements.lock.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, version = line.split("==")
        wheel = wheel_index[(canonicalize_name(name), version)]
        requirements.append(f"{line} --hash=sha256:{digest(wheel)}")
    (baseline / "requirements.macos-x86_64.hashed.txt").write_text(
        "# Exact wheels tested on CPython 3.12 / macOS x86_64.\n"
        + "\n".join(requirements) + "\n", encoding="utf-8",
    )
    (baseline / "wheels.json").write_text(json.dumps({
        "scope": "CPython 3.12 / macOS x86_64; other platforms require their own wheel verification",
        "local_wheelhouse": str(wheelhouse.relative_to(repo)),
        "wheels": [{"filename": wheel.name, "sha256": digest(wheel), "bytes": wheel.stat().st_size}
                   for wheel in wheels],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(path for path in baseline.rglob("*") if path.is_file()
                   and "__pycache__" not in path.parts and not path.name.startswith("._"))
    for path in files:
        if path.is_symlink() or path.name == ".env":
            raise ValueError(f"Unsealed private or linked file: {path.name}")
    (baseline / "SHA256SUMS").write_text("".join(
        f"{digest(path)}  {path.relative_to(baseline).as_posix()}\n" for path in files
    ), encoding="utf-8")
    print(json.dumps({"sealed_files": len(files), "wheels": len(wheels), "directory": str(baseline)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--prepare-tools-only", action="store_true", help="Copy the fixed tools before their independent smoke test")
    args = parser.parse_args()
    if args.prepare_tools_only:
        prepare_tools(args.repo.resolve(), args.baseline.resolve())
    else:
        seal(args.repo.resolve(), args.baseline.resolve(), args.wheelhouse.resolve())
