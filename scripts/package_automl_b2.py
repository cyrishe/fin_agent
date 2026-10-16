"""Package B2 source and immutable seed; never include environment credentials."""
from __future__ import annotations
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "outputs/stock_automl/b2_assets_20261015"
OUTPUT = ROOT / "outputs/stock_automl/deployment/b2-20261015.tar.gz"


def closure():
    pending = ["run_automl_b2", "verify_automl_b2", "backfill_automl_b2"]; seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((ROOT / "scripts" / (name + ".py")).read_text())):
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module else [a.name for a in node.names] if isinstance(node, ast.Import) else []
            pending.extend(m.split(".")[1] for m in mods if m.startswith("scripts."))
    return [ROOT / "scripts" / (name + ".py") for name in sorted(seen)]


def main():
    sources = closure() + [ROOT / "deploy/sql/automl_1440_strategy.sql", ROOT / "deploy/sql/strategy_execution.sql"]
    pairs = [(p, str(p.relative_to(ROOT))) for p in sources]
    pairs += [(p, p.name) for p in (ROOT / "deploy/stock-automl-b2").iterdir() if p.is_file()]
    seed = json.loads((ASSETS / "SEED_MANIFEST.json").read_text())
    for name, expected in seed["files"].items():
        p = ASSETS / name
        assert hashlib.sha256(p.read_bytes()).hexdigest() == expected, name
        pairs.append((p, "assets/" + name))
    for name in ("SEED_MANIFEST.json", "verification.json"):
        pairs.append((ASSETS / name, "assets/" + name))
    monthly = ASSETS / "verification_runs/verification_monthly.json"
    assert monthly.exists(), "Monthly retraining reproduction must pass before packaging"
    pairs.append((monthly, "assets/verification_monthly.json"))
    # Keep the experiment evidence alongside the runnable model seed.
    for folder in sorted((ROOT / "docs/stock_automl_runs").glob("20261015_limit_rebound_*")):
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.suffix in (".md", ".json", ".csv", ".xlsx") and p.stat().st_size < 10_000_000:
                pairs.append((p, str(p.relative_to(ROOT))))
    manifest = {name: hashlib.sha256(p.read_bytes()).hexdigest() for p, name in pairs}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("wb") as f, gzip.GzipFile(filename="", fileobj=f, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for p, name in pairs:
            info = tarfile.TarInfo(name); info.size = p.stat().st_size; info.mode = 0o755 if name == "b2.sh" else 0o644; info.mtime = 0
            with p.open("rb") as source:
                tar.addfile(info, source)
        payload = json.dumps(manifest, indent=2, sort_keys=True).encode()
        info = tarfile.TarInfo("MANIFEST.json"); info.size = len(payload); info.mode = 0o644; info.mtime = 0
        tar.addfile(info, io.BytesIO(payload))
    receipt = {"package": str(OUTPUT), "bytes": OUTPUT.stat().st_size,
               "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(), "files": len(pairs)}
    OUTPUT.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
