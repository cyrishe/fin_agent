import json
from pathlib import Path

import pytest

from src.services.custom_tool_service import (
    CustomToolError,
    CustomToolRuntimeService,
    CustomToolStoreService,
)
from src.services.python_execution_runtime import PythonExecutionRuntime


OWNER_ID = "user_candidate_owner"
TOOL_NAME = "ct_candidate_threshold"


def _design(limit: int, *, tool_name: str = TOOL_NAME) -> dict:
    return {
        "manifest": {
            "tool_name": tool_name,
            "display_name": "候选阈值工具",
            "description": f"判断数值是否不超过 {limit}。",
            "visibility": "personal",
            "runtime": {
                "kind": "python_sandbox",
                "backend": "local_dev",
                "timeout_ms": 2000,
            },
        },
        "input_schema": {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "number", "maximum": limit}},
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": ["accepted"],
            "properties": {"accepted": {"type": "boolean"}},
        },
        "code": (
            "def run(inputs: dict) -> dict:\n"
            f"    return {{'accepted': float(inputs['value']) <= {limit}}}\n"
        ),
        "design_contract": {
            "tool_name": tool_name,
            "document": f"阈值为 {limit}。",
        },
        "sample_input": {"value": 8},
    }


def _active_store(tmp_path: Path) -> CustomToolStoreService:
    store = CustomToolStoreService(
        root_dir=str(tmp_path / "tools"),
        backend="filesystem",
    )
    store.save_draft(_design(10), owner_id=OWNER_ID)
    store.commit(TOOL_NAME, owner_ids=[OWNER_ID])
    return store


def _runtime(store: CustomToolStoreService, tmp_path: Path) -> CustomToolRuntimeService:
    return CustomToolRuntimeService(
        store=store,
        python_runtime=PythonExecutionRuntime(allow_unsafe_backends=True),
        runtime_root=str(tmp_path / "runtime"),
    )


def test_unactivated_candidate_keeps_previous_active_revision_runnable(
    tmp_path: Path,
) -> None:
    store = _active_store(tmp_path)

    candidate = store.save_candidate_revision(
        _design(5),
        owner_id=OWNER_ID,
        tool_name=TOOL_NAME,
    )

    assert candidate["manifest"]["current_revision"] == 2
    assert candidate["manifest"]["active_revision"] == 1
    assert candidate["manifest"]["status"] == "draft"
    assert candidate["storage"]["is_active"] is False
    active = store.load_for_runtime(TOOL_NAME, owner_ids=[OWNER_ID])
    assert active["manifest"]["current_revision"] == 1
    assert "<= 10" in active["code"]
    result = _runtime(store, tmp_path).run(
        TOOL_NAME,
        {"value": 8},
        owner_ids=[OWNER_ID],
        allow_inactive=False,
    )
    assert result["ok"] is True
    assert result["data"] == {"accepted": True}


def test_activate_revision_switches_complete_bundle_after_optimistic_check(
    tmp_path: Path,
) -> None:
    store = _active_store(tmp_path)
    candidate = store.save_candidate_revision(
        _design(5),
        owner_id=OWNER_ID,
        tool_name=TOOL_NAME,
    )

    activated = store.activate_revision(
        TOOL_NAME,
        candidate["manifest"]["current_revision"],
        expected_active_revision=1,
        owner_id=OWNER_ID,
    )

    assert activated["manifest"]["status"] == "active"
    assert activated["manifest"]["current_revision"] == 2
    assert activated["input_schema"]["required"] == ["value"]
    assert activated["design_contract"]["document"] == "阈值为 5。"
    result = _runtime(store, tmp_path).run(
        TOOL_NAME,
        {"value": 8},
        owner_ids=[OWNER_ID],
        allow_inactive=False,
    )
    assert result["ok"] is True
    assert result["data"] == {"accepted": False}


def test_filesystem_candidate_keeps_entry_module_equal_to_authoritative_code(
    tmp_path: Path,
) -> None:
    store = _active_store(tmp_path)
    design = _design(5)
    design["modules"] = [{
        "module_id": "main",
        "language": "python",
        "entrypoint": "run",
        "source_code": "def run(inputs): return {'accepted': True}",
    }]

    candidate = store.save_candidate_revision(
        design,
        owner_id=OWNER_ID,
        tool_name=TOOL_NAME,
    )

    assert "<= 5" in candidate["code"]
    assert "<= 5" in candidate["modules"][0]["source_code"]


def test_active_load_follows_manifest_pointer_during_file_switch(tmp_path: Path) -> None:
    store = _active_store(tmp_path)
    candidate = store.save_candidate_revision(
        _design(5), owner_id=OWNER_ID, tool_name=TOOL_NAME
    )
    root = Path(candidate["root"])

    # Simulate activation having copied candidate compatibility files but not
    # yet switched the root manifest pointer.
    root.joinpath("tool.py").write_text(candidate["code"], encoding="utf-8")
    root.joinpath("input_schema.json").write_text(
        json.dumps(candidate["input_schema"]), encoding="utf-8"
    )
    root.joinpath("spec.json").write_text(
        json.dumps(
            {
                "design_contract": candidate["design_contract"],
                "sample_input": candidate["sample_input"],
            }
        ),
        encoding="utf-8",
    )

    active = store.load(TOOL_NAME)

    assert active["manifest"]["current_revision"] == 1
    assert "<= 10" in active["code"]
    assert active["input_schema"]["properties"]["value"]["maximum"] == 10
    assert active["design_contract"]["document"] == "阈值为 10。"


def test_stale_activation_is_rejected_and_does_not_replace_active_revision(
    tmp_path: Path,
) -> None:
    store = _active_store(tmp_path)
    first_candidate = store.save_candidate_revision(
        _design(5), owner_id=OWNER_ID, tool_name=TOOL_NAME
    )
    second_candidate = store.save_candidate_revision(
        _design(3), owner_id=OWNER_ID, tool_name=TOOL_NAME
    )
    store.activate_revision(
        TOOL_NAME,
        first_candidate["manifest"]["current_revision"],
        expected_active_revision=1,
        owner_id=OWNER_ID,
    )

    with pytest.raises(CustomToolError, match="active custom tool revision changed"):
        store.activate_revision(
            TOOL_NAME,
            second_candidate["manifest"]["current_revision"],
            expected_active_revision=1,
            owner_id=OWNER_ID,
        )
    with pytest.raises(CustomToolError, match="base revision changed"):
        store.activate_revision(
            TOOL_NAME,
            second_candidate["manifest"]["current_revision"],
            expected_active_revision=2,
            owner_id=OWNER_ID,
        )

    assert store.load(TOOL_NAME)["manifest"]["current_revision"] == 2
    assert "<= 5" in store.load(TOOL_NAME)["code"]


def test_activation_rejects_owner_mismatch_and_missing_candidate(
    tmp_path: Path,
) -> None:
    store = _active_store(tmp_path)

    with pytest.raises(CustomToolError, match="not owned"):
        store.activate_revision(
            TOOL_NAME,
            2,
            expected_active_revision=1,
            owner_id="different_user",
        )
    with pytest.raises(CustomToolError, match="revision not found"):
        store.activate_revision(
            TOOL_NAME,
            999,
            expected_active_revision=1,
            owner_id=OWNER_ID,
        )

    assert store.load_for_runtime(
        TOOL_NAME, owner_ids=[OWNER_ID]
    )["manifest"]["current_revision"] == 1


def test_candidate_save_rejects_model_tool_identity_drift(tmp_path: Path) -> None:
    store = _active_store(tmp_path)

    with pytest.raises(CustomToolError, match="identity changed"):
        store.save_candidate_revision(
            _design(5, tool_name="ct_model_changed_name"),
            owner_id=OWNER_ID,
            tool_name=TOOL_NAME,
        )

    assert store.load(TOOL_NAME)["manifest"]["current_revision"] == 1
