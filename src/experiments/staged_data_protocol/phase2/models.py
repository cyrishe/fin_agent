from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class Step:
    step_id: str
    subject: str
    dataview: str
    condition_desc: str
    is_output: bool = False
    raw: str = ""


@dataclass
class ApiCall:
    result_id: str
    api: str
    args: Dict[str, Any]
    outputs: List[str]
    raw: str


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ResultHandle:
    name: str
    api: str
    columns: List[str]
    data: Any = None
    step_id: str = ""
    task: str = ""
