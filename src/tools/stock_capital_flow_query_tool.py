from __future__ import annotations

from typing import Any, Dict

from src.tools.capital_flow_query_tool import run_fixed_subject_type


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    return run_fixed_subject_type(
        args,
        subject_type="stock",
        tool_name="stock_capital_flow_query",
    )
