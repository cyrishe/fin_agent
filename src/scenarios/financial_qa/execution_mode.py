from __future__ import annotations

from typing import Any


FINANCIAL_QA_EXECUTION_MODE_STANDARD = "standard"
FINANCIAL_QA_EXECUTION_MODE_FAST = "fast"
FINANCIAL_QA_EXECUTION_MODES = frozenset(
    {
        FINANCIAL_QA_EXECUTION_MODE_STANDARD,
        FINANCIAL_QA_EXECUTION_MODE_FAST,
    }
)


def normalize_financial_qa_execution_mode(value: Any) -> str:
    """Normalize the explicit DSH orchestration policy for one query.

    This is separate from ``research_mode``: execution mode controls whether
    the agent may inspect, repair, or retry, while research mode controls the
    requested answer depth after data has been obtained.
    """

    normalized = str(value or FINANCIAL_QA_EXECUTION_MODE_STANDARD).strip().lower()
    if normalized not in FINANCIAL_QA_EXECUTION_MODES:
        raise ValueError("execution_mode 仅支持 standard 或 fast")
    return normalized
