from __future__ import annotations

import json
from pathlib import Path

from src.tools.quant_factor_screening_tool import run
from src.tools.registry import list_tools, run_tool


def test_quant_factor_screening_tool_registered_and_safe_gap_response(tmp_path):
    result = run_tool(
        "quant_factor_screening",
        {
            "user_text": "找机器人概念里资金和量价最强的前20只",
            "universe": ["600000"],
            "date_range": {"start": "2026-05-29", "end": "2026-05-29"},
            "_runtime": {"data_root": str(tmp_path / "data")},
        },
    )

    assert "quant_factor_screening" in list_tools()
    assert result["tool"] == "quant_factor_screening"
    assert result["ok"] is False
    assert result["meta"]["failure_kind"] == "data_unavailable"
    assert result["data"]["selected_stocks"] == []
    payload_text = json.dumps(result, ensure_ascii=False)
    assert "mysql://" not in payload_text.lower()
    assert "password" not in payload_text.lower()
    assert "收益承诺" in payload_text


def test_quant_factor_screening_skill_files_exist():
    root = Path("src/skills/quant_factor_screening")
    assert (root / "skill.json").exists()
    assert (root / "SKILL.md").exists()
    assert (root / "schema.json").exists()

    skill = json.loads((root / "skill.json").read_text(encoding="utf-8"))
    assert skill["skill_name"] == "quant_factor_screening"
    assert "quant_factor_screening" in skill["tools"]
    assert skill["expected_render_page_type"] == "quant_factor_screening"


def test_direct_tool_returns_structured_error_for_empty_universe(tmp_path):
    result = run(
        {
            "user_text": "最近20个交易日涨幅靠前",
            "universe": [],
            "required_data": ["daily_price"],
            "_runtime": {"data_root": str(tmp_path / "data")},
        }
    )

    assert result["ok"] is False
    assert result["meta"]["failure_kind"] == "empty_universe"
