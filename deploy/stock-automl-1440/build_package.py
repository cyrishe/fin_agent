"""Build a minimal source tarball for the Linux 14:40 job; no credentials."""
from __future__ import annotations

import ast
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs/stock_automl/deployment"
START = {"run_automl_1440_strategy", "check_backfill_automl_previous_day"}


def script_closure() -> list[Path]:
    pending, seen = list(START), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        path = ROOT / "scripts" / f"{name}.py"
        if not path.is_file():
            raise FileNotFoundError(path)
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            pending.extend(module.split(".")[1] for module in modules
                           if module.startswith("scripts."))
    return [ROOT / "scripts" / f"{name}.py" for name in sorted(seen)]


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    files = script_closure() + [
        ROOT / "deploy/sql/automl_1440_strategy.sql",
        ROOT / "deploy/sql/strategy_execution.sql",
        HERE / "requirements-linux-py312.txt",
        HERE / "automl1440.sh",
        HERE / "install_runtime.sh",
        HERE / "README.md",
    ]
    names = [path.relative_to(ROOT).as_posix().removeprefix("deploy/stock-automl-1440/")
             for path in files]
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in zip(names, files)}
    tar_path = OUTPUT / "automl1440-execution-20261015-source.tar.gz"
    with tar_path.open("wb") as output, gzip.GzipFile(
            filename="", mode="wb", fileobj=output, mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode="w") as tar:
        for path, name in zip(files, names):
            payload = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755 if name in ("automl1440.sh", "install_runtime.sh") else 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(payload))
        payload = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
        info = tarfile.TarInfo("MANIFEST.json")
        info.size = len(payload)
        info.mode = 0o644
        info.mtime = 0
        tar.addfile(info, io.BytesIO(payload))
    print(json.dumps({"tarball": str(tar_path), "sha256": hashlib.sha256(tar_path.read_bytes()).hexdigest(),
                      "scripts": len(files)-5, "bytes": tar_path.stat().st_size}))


if __name__ == "__main__":
    main()
