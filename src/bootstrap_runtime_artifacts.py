import argparse
import json

from src.services.runtime_artifact_service import RuntimeArtifactService


def main() -> int:
    parser = argparse.ArgumentParser(description="同步设计态资产到 aiia_runtime_artifact")
    parser.add_argument(
        "--source-type",
        default="file_sync",
        help="revision source_type，默认 file_sync",
    )
    parser.add_argument(
        "--changed-by",
        default="bootstrap_runtime_artifacts",
        help="revision created_by/updated_by",
    )
    args = parser.parse_args()

    service = RuntimeArtifactService()
    results = service.sync_all_design_time_artifacts(
        source_type=str(args.source_type or "file_sync").strip() or "file_sync",
        changed_by=str(args.changed_by or "bootstrap_runtime_artifacts").strip()
        or "bootstrap_runtime_artifacts",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
