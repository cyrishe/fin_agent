import json

from src.services.code_work_item_runner import CodeWorkItemRunner
from src.services.file_artifact_service import FileArtifactService
from src.services.python_execution_runtime import PythonExecutionRuntime


def _runner(tmp_path, artifact_service):
    return CodeWorkItemRunner(
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runs"),
        file_artifact_service=artifact_service,
    )


def _step(code):
    return {
        "step_id": "step_artifact",
        "type": "code",
        "name": "analysis_python",
        "runtime_profile": {"backend": "local_dev", "limits": {"timeout_ms": 2000, "output_json_bytes": 100000}},
        "code_task_spec": {"task_kind": "table_analysis", "solution_mode": "generated_inline", "code": code},
        "output_binding": {},
    }


def test_code_runtime_materializes_table_artifact_into_inputs(tmp_path):
    service = FileArtifactService(data_root=tmp_path / "data")
    manifest = service.write_table_artifact(
        columns=[{"name": "name", "type": "string", "index": 0}, {"name": "score", "type": "integer", "index": 1}],
        rows=[{"name": "A", "score": 3}, {"name": "B", "score": 7}],
        preview_rows=[{"name": "A", "score": 3}],
        source_file={"source_type": "upload", "source_id": "att_table", "file_name": "scores.csv", "mime_type": "text/csv", "size_bytes": 20, "sha256": "x"},
        created_by_tool="file_read_csv",
        table_id="table1",
    )
    code = """
import json
import os

with open(os.environ["CODE_INPUT_JSON"], "r", encoding="utf-8") as f:
    payload = json.load(f)
artifact = payload["artifact_inputs"]["table"]
with open(os.path.join(os.environ["CODE_INPUT_DIR"], artifact["data_path"]), "r", encoding="utf-8") as f:
    rows = [json.loads(line) for line in f if line.strip()]
out = {
    "tool": "analysis_python",
    "ok": True,
    "data": {"structured_data": {"row_count": len(rows), "top": rows[-1]["name"]}, "render_blocks": []},
    "error": ""
}
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
"""

    run = _runner(tmp_path, service).run(step=_step(code), resolved_inputs={"table": manifest["artifact_ref"]})

    assert run["status"] == "completed"
    assert run["result"]["data"]["structured_data"] == {"row_count": 2, "top": "B"}
    input_artifact = run["diagnostics"]["input_artifact"]["path"]
    with open(f"{input_artifact}/input.json", "r", encoding="utf-8") as f:
        input_payload = json.load(f)
    artifact_input = input_payload["artifact_inputs"]["table"]
    assert artifact_input["artifact_ref"] == manifest["artifact_ref"]
    assert artifact_input["manifest_path"].startswith("artifacts/art_")
    assert artifact_input["data_path"].startswith("artifacts/art_")
    assert not artifact_input["data_path"].startswith("/")


def test_code_runtime_rejects_artifact_with_escaping_manifest(tmp_path):
    service = FileArtifactService(data_root=tmp_path / "data")
    artifact_dir = tmp_path / "data" / "runtime_file_artifacts" / "art_bad"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "file_artifact.v1",
                "artifact_id": "art_bad",
                "artifact_ref": "runtime://artifact/art_bad",
                "kind": "table",
                "files": {"data": "../escape.jsonl"},
            }
        ),
        encoding="utf-8",
    )

    run = _runner(tmp_path, service).run(step=_step("print('should not run')"), resolved_inputs={"table": "runtime://artifact/art_bad"})

    assert run["status"] == "failed"
    assert run["failure_kind"] == "invalid_file_id"


def test_file_artifact_materialize_copies_only_manifest_referenced_files(tmp_path):
    service = FileArtifactService(data_root=tmp_path / "data")
    manifest = service.write_table_artifact(
        columns=[{"name": "name", "type": "string", "index": 0}],
        rows=[{"name": "A"}],
        preview_rows=[{"name": "A"}],
        source_file={"source_type": "upload", "source_id": "att_table", "file_name": "scores.csv", "mime_type": "text/csv", "size_bytes": 20, "sha256": "x"},
        created_by_tool="file_read_csv",
        table_id="table1",
    )
    artifact_dir = tmp_path / "data" / "runtime_file_artifacts" / manifest["artifact_id"]
    (artifact_dir / "not_in_manifest.txt").write_text("secret", encoding="utf-8")

    materialized = service.materialize(manifest["artifact_ref"], input_dir=tmp_path / "inputs")

    target_dir = tmp_path / "inputs" / "artifacts" / manifest["artifact_id"]
    assert materialized["data_path"] == f"artifacts/{manifest['artifact_id']}/data.jsonl"
    assert (target_dir / "manifest.json").exists()
    assert (target_dir / "data.jsonl").exists()
    assert not (target_dir / "not_in_manifest.txt").exists()
