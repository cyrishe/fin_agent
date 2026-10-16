#!/usr/bin/env python3
"""Build and verify the explicitly reconstructed, isolated CC reference sources.

Only Git objects at SOURCE_COMMIT are read. Local source edits, environment files,
runtime records and installed dependencies never enter the archive.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


SOURCE_COMMIT = "2242990c9a092c00a4cd1bb8a0bbb42d9fbc7b7f"
CATALOG_PATH = "src/tools/finance_data/catalog/api_view_catalog.json"
CATALOG_SHA256 = "aa5713c6946a5aa1a802b0be62c6d77dec0f94bd6c33cf0b7b5b3006a39335a3"
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_ARCHIVE_DIR = ROOT if SCRIPT_DIR.name == "tools" else ROOT / "baselines/cc_20260902_rebuilt"
SOURCE_DIRECTORIES = ("src", "config", "frontend")
SOURCE_FILES = (
    "AGENTS.md", "README.md", "requirements.txt", "stock_name.tsv",
    "main_framework.md", "custom_tools.md", "phrase_1_prompt.md",
    "phase1_check_repair_appendix.md", "phase2_api_prompt.md",
    "phase2_dynamic_cal_code_prompt.md", "phase2_final_check_prompt.md",
    "SKILL_requirement_coding.md", "SKILL_requirement_understanding.md",
    "SKILL_requirement_understanding_event_format.md",
    "scripts/dsh_source_runtime.py",
    # Reviewed static identifier table, not runtime or customer data.
    "data/index_subjects.tsv",
)
RECONSTRUCTION_NOTE = (
    "Reconstructed CC reference for the completed 2026-09-02 comparison, using "
    "the first integrated Git commit from 2026-09-04. This is not an exact "
    "2026-09-02 workspace snapshot. The catalog SHA256 matches the historical "
    "CC result references; complete historical prompts, dependencies and "
    "environment were not hash-bound. No current production source is used."
)
_BLOCKED_PARTS = {".git", "__pycache__", ".pytest_cache", "node_modules", "dist", "outputs", "output", ".venv"}
_SECRET_KEY = re.compile(r"(?:api.?key|secret|password|passwd|access.?token|auth.?token|private.?key)", re.I)
_PLACEHOLDER = re.compile(
    r"^(?:|password(?:[-_]\d+)?|passwd|test.*|dummy.*|fake.*|placeholder.*|your[_ -].*|"
    r"change[_ -].*|replace[_ -].*|dev.*|mock.*|api[_ -]?key|secret|<.*>|\$\{.*\}|\*+)$", re.I,
)


class ArchiveError(ValueError):
    """The reference source or archive failed a required integrity/safety check."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _allowed_path(name: str) -> bool:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or "\\" in name or "\x00" in name:
        return False
    if any(part in (".", "..") for part in name.split("/")) or str(path) != name:
        return False
    if any(part in _BLOCKED_PARTS or part.startswith((".env", "._")) for part in path.parts):
        return False
    if path.suffix in {".pyc", ".pyo", ".log", ".sqlite", ".db"}:
        return False
    return name in SOURCE_FILES or path.parts[0] in SOURCE_DIRECTORIES


def _git(repo: Path, *arguments: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        # Git stderr can contain local configuration/URLs; never echo it here.
        raise ArchiveError("Unable to read the fixed reference Git objects") from exc


def _literal_pairs(tree: ast.AST):
    for node in ast.walk(tree):
        pairs = []
        if isinstance(node, ast.Assign):
            pairs = [(ast.unparse(target), node.value) for target in node.targets]
        elif isinstance(node, ast.AnnAssign):
            pairs = [(ast.unparse(node.target), node.value)]
        elif isinstance(node, ast.keyword):
            pairs = [(node.arg or "", node.value)]
        elif isinstance(node, ast.Dict):
            pairs = [(key.value, value) for key, value in zip(node.keys, node.values)
                     if isinstance(key, ast.Constant) and isinstance(key.value, str)]
        elif isinstance(node, ast.arguments):
            if node.defaults:
                pairs = [(arg.arg, value) for arg, value in zip(node.args[-len(node.defaults):], node.defaults)]
            pairs += [(arg.arg, value) for arg, value in zip(node.kwonlyargs, node.kw_defaults)]
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in {"getenv", "get"} and len(node.args) > 1
              and isinstance(node.args[0], ast.Constant)):
            pairs = [(str(node.args[0].value), node.args[1])]
        for key, value in pairs:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield key, value.value, value.lineno


def scan_secrets(name: str, content: bytes) -> list[str]:
    """Conservative literal heuristic; return locations/keys only, never values.

    This is a packaging guard, not a claim that heuristic scanning proves the
    absence of every possible secret. The fixed source allowlist is also reviewed.
    """
    text = content.decode("utf-8", errors="replace")
    findings = []
    for pattern, key in (
        (r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", "private-key"),
        (r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}", "api-token"),
        (r"(?:mysql|postgres(?:ql)?|redis)(?:\+\w+)?://[^\s:/]+:[^\s@]+@", "credential-url"),
    ):
        for match in re.finditer(pattern, text):
            findings.append(f"{name}:{text.count(chr(10), 0, match.start()) + 1}:{key}")
    if name.endswith(".py"):
        try:
            pairs = _literal_pairs(ast.parse(text))
            for key, value, line in pairs:
                if _SECRET_KEY.search(key) and not _PLACEHOLDER.fullmatch(value):
                    findings.append(f"{name}:{line}:{key}")
        except SyntaxError as exc:
            raise ArchiveError(f"Cannot scan Python source: {name}:{exc.lineno}") from exc
    else:
        # Key/value syntax, not arbitrary text such as HTML password autocomplete.
        pattern = r'''(?im)(?:^|[,{])\s*["']?([\w.-]*(?:api[_-]?key|password|passwd|secret|access[_-]?token)[\w.-]*)["']?\s*[:=]\s*["']([^"'\n]+)["']'''
        for match in re.finditer(pattern, text):
            if not _PLACEHOLDER.fullmatch(match.group(2)):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{name}:{line}:{match.group(1)}")
    return sorted(set(findings))


def build_archive(repo: Path, output_dir: Path) -> dict:
    """Create deterministic source.tar.gz and manifest.json; never overwrite them."""
    repo, output_dir = Path(repo), Path(output_dir)
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise ArchiveError("Archive output must be a directory, not a symlink/file")
    if any((output_dir / name).exists() for name in ("source.tar.gz", "manifest.json")):
        raise ArchiveError("Archive output files already exist")
    records = _git(repo, "ls-tree", "-rz", SOURCE_COMMIT).split(b"\x00")
    entries = []
    for record in records:
        if not record:
            continue
        header, raw_name = record.split(b"\t", 1)
        name = raw_name.decode("utf-8")
        if not _allowed_path(name):
            continue
        mode, kind, _ = header.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ArchiveError(f"Unsupported source entry: {name}")
        entries.append((name, 0o755 if mode == "100755" else 0o644))
    names = {name for name, _ in entries}
    if not set(SOURCE_FILES).issubset(names) or CATALOG_PATH not in names:
        raise ArchiveError("Fixed reference is missing required source assets")
    entries.sort()
    # Bounded parallel Git reads avoid hundreds of serial process launches.
    with ThreadPoolExecutor(max_workers=8) as pool:
        contents = list(pool.map(lambda entry: _git(repo, "show", f"{SOURCE_COMMIT}:{entry[0]}"), entries))
    findings = [finding for (name, _), content in zip(entries, contents) for finding in scan_secrets(name, content)]
    if findings:
        raise ArchiveError("Possible source secrets; review locations only:\n" + "\n".join(findings))
    files = {name: {"sha256": _sha256(content), "size": len(content), "mode": mode}
             for (name, mode), content in zip(entries, contents)}
    if files[CATALOG_PATH]["sha256"] != CATALOG_SHA256:
        raise ArchiveError("Catalog does not match the historical CC result revision")
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, filename="", mode="wb", mtime=0) as gzip_file:
        with tarfile.open(fileobj=gzip_file, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for (name, mode), content in zip(entries, contents):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(content), mode, 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(content))
    archive_bytes = compressed.getvalue()
    manifest = {
        "format_version": 1,
        "reference_name": "cc_20260902_rebuilt",
        "source_commit": SOURCE_COMMIT,
        "source_commit_date": _git(repo, "show", "-s", "--format=%cI", SOURCE_COMMIT).decode().strip(),
        "reconstruction_note": RECONSTRUCTION_NOTE,
        "catalog_sha256": CATALOG_SHA256,
        "source_archive": {"name": "source.tar.gz", "sha256": _sha256(archive_bytes)},
        "source_allowlist": {"directories": list(SOURCE_DIRECTORIES), "files": list(SOURCE_FILES)},
        "excluded": "Environment/credentials, runtime or customer data, outputs, caches, dependencies and Git metadata",
        "secret_scan": "Reviewed fixed source allowlist and literal credential heuristic; no findings",
        "files": files,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "source.tar.gz").open("xb") as handle:
        handle.write(archive_bytes)
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return manifest


def _verified_contents(archive_dir: Path) -> tuple[dict, dict[str, bytes]]:
    archive_dir = Path(archive_dir)
    try:
        manifest = json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("format_version") != 1 or manifest.get("source_commit") != SOURCE_COMMIT
                or manifest.get("catalog_sha256") != CATALOG_SHA256):
            raise ArchiveError("Unexpected reference identity or catalog digest")
        archive_meta, files = manifest["source_archive"], manifest["files"]
        if archive_meta["name"] != "source.tar.gz" or not isinstance(files, dict):
            raise ArchiveError("Invalid archive manifest")
        if not set(SOURCE_FILES).issubset(files) or CATALOG_PATH not in files:
            raise ArchiveError("Manifest is missing required source assets")
        for name, entry in files.items():
            if not _allowed_path(name) or entry["mode"] not in {0o644, 0o755}:
                raise ArchiveError(f"Unsafe manifest entry: {name}")
        archive_bytes = (archive_dir / "source.tar.gz").read_bytes()
        if _sha256(archive_bytes) != archive_meta["sha256"]:
            raise ArchiveError("Archive SHA256 mismatch")
        contents = {}
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            for member in archive:
                name = member.name
                if not _allowed_path(name) or not member.isfile() or name not in files or name in contents:
                    raise ArchiveError(f"Unsafe or unexpected archive entry: {name}")
                entry = files[name]
                if member.size != entry["size"] or member.mode != entry["mode"]:
                    raise ArchiveError(f"Source metadata mismatch: {name}")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ArchiveError(f"Unreadable archive entry: {name}")
                content = handle.read()
                if _sha256(content) != entry["sha256"]:
                    raise ArchiveError(f"Source SHA256 mismatch: {name}")
                contents[name] = content
        if set(contents) != set(files):
            raise ArchiveError("Archive file inventory mismatch")
        if _sha256(contents[CATALOG_PATH]) != CATALOG_SHA256:
            raise ArchiveError("Historical catalog SHA256 mismatch")
        return manifest, contents
    except (OSError, KeyError, TypeError, UnicodeError, json.JSONDecodeError, tarfile.TarError, EOFError) as exc:
        raise ArchiveError("Cannot read a valid reference archive/manifest") from exc


def verify_archive(archive_dir: Path) -> dict:
    """Verify archive, each file and paths in memory, without extracting anything.

    Hashes provide integrity relative to the reviewed manifest, not a signature.
    Keep the manifest and builder in a trusted Git revision.
    """
    return _verified_contents(archive_dir)[0]


def extract_archive(archive_dir: Path, destination: Path) -> dict:
    """Verify fully, then extract regular files into a new or empty directory."""
    destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    )):
        raise ArchiveError("Extraction destination must be a new or empty directory")
    manifest, contents = _verified_contents(archive_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        path = destination.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(content)
        path.chmod(manifest["files"][name]["mode"])
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Package the fixed Git source, never local files")
    build.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    build.add_argument("--output", type=Path, default=DEFAULT_ARCHIVE_DIR)
    verify = subparsers.add_parser("verify", help="Verify without extracting")
    verify.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    extract = subparsers.add_parser("extract", help="Verify then safely extract to a new empty directory")
    extract.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR)
    extract.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            manifest = build_archive(args.repo, args.output)
        elif args.command == "verify":
            manifest = verify_archive(args.archive_dir)
        else:
            manifest = extract_archive(args.archive_dir, args.destination)
    except ArchiveError as exc:
        parser.exit(1, f"Reference archive error: {exc}\n")
    print(json.dumps({"ok": True, "source_commit": manifest["source_commit"],
                      "files": len(manifest["files"]),
                      "archive_sha256": manifest["source_archive"]["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
