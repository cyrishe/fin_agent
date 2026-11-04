"""Build the public evaluation client from an explicit, credential-free file list."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "run_eval.sh": "run_eval.sh",
    "requirements-eval.txt": "requirements-eval.txt",
    "docs/mcp_eval_client.md": "README.md",
    "scripts/eval_finance_mcp.py": "scripts/eval_finance_mcp.py",
    "scripts/finance_mcp_report.py": "scripts/finance_mcp_report.py",
    "scripts/finance_mcp_report.mjs": "scripts/finance_mcp_report.mjs",
    "tests/evals/report_mcp_skill_smoke_v1.json": "tests/evals/report_mcp_skill_smoke_v1.json",
}


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, target in FILES.items():
            archive.write(ROOT / source, "finance_eval_client/" + target)
    return output.resolve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/finance_eval_client/finance_eval_client.zip")
    print(build(parser.parse_args().output))
