"""Same historical questions on captured DSH checkouts; catalog/prompt A/B only.

Reuses the isolated, credential-cleaning runner from the five-case comparison.
No frozen CC assets or server state are changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import compare_cc_reference_dsh_five as runner


CASE_IDS = ["BUS010", "BUS014", "BUS026", "BUS093", "BUS157", "BUS168",
            "BUS141", "BUS217", "RTEF070", "RTEF112", "RTEF012", "BUS185"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.prepare:
        runner.prepare(output)
        sources = [
            "outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json",
            "outputs/financial_qa_mainland_full_increment_20260903/cases_mainland_full_no_news.json",
        ]
        index = {}
        for source in sources:
            for row in json.loads((runner.REPO / source).read_text())["cases"]:
                index[row["case_id"]] = {"case_id": row["case_id"], "question": row["question"],
                                         "source_file": source}
        runner.write(output / "cases.json", {"cases": [index[key] for key in CASE_IDS]})
        manifest = json.loads((output / "manifest.json").read_text())
        manifest["evaluation"] = "DSH catalog/prompt refinement, unchanged historical questions"
        manifest["case_ids"] = CASE_IDS
        runner.write(output / "manifest.json", manifest)
        return
    raise SystemExit(subprocess.call([
        sys.executable, str(Path(runner.__file__).resolve()), "--output", str(output), "--runtime", "dsh",
    ]))


if __name__ == "__main__":
    main()
