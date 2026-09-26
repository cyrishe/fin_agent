from __future__ import annotations

from typing import Any


FINANCIAL_QA_RUNTIME_CC = "cc"
FINANCIAL_QA_RUNTIME_DSH = "dsh"
FINANCIAL_QA_RUNTIMES = frozenset(
    {FINANCIAL_QA_RUNTIME_CC, FINANCIAL_QA_RUNTIME_DSH}
)


def normalize_financial_qa_runtime(value: Any) -> str:
    """Resolve the explicit financial-QA execution path.

    The default remains CC for backward compatibility.  This is a runtime
    routing fact consumed by the chat entry and is not inferred from the
    user's natural-language question.
    """

    normalized = str(value or FINANCIAL_QA_RUNTIME_CC).strip().lower()
    if normalized not in FINANCIAL_QA_RUNTIMES:
        raise ValueError("financial_qa_runtime 仅支持 cc 或 dsh")
    return normalized
