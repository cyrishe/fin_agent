import re
from typing import Any, Dict

from src.subject_catalog import SubjectCatalog


class EntityResolver:
    """
    Entrance-layer entity normalization.

    Current phase keeps this deterministic and lightweight:
    - extract 6-digit stock code
    - carry through explicit company/name/concept fields
    - produce a stable normalized entity object
    """

    STOCK_CODE_RE = re.compile(r"\b\d{6}\b")

    def resolve(self, *, user_text: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        context = context or {}
        text = str(user_text or "").strip()
        explicit_code = str(context.get("code") or "").strip()
        explicit_name = str(context.get("name") or context.get("company") or "").strip()
        concept = str(context.get("concept") or "").strip()
        matched_subject = {} if (explicit_code or explicit_name) else self._extract_stock_subject(text)
        code = explicit_code or str(matched_subject.get("code") or "").strip() or self._extract_stock_code(text)
        explicit_name = explicit_name or str(matched_subject.get("name") or "").strip()
        subject_type = "unknown"
        if code or explicit_name:
            subject_type = "stock"
        if concept:
            subject_type = "concept"
        return {
            "subject_type": subject_type,
            "code": code,
            "name": explicit_name,
            "concept": concept,
            "query_text": text,
            "resolved": bool(code or explicit_name or concept),
        }

    def _extract_stock_code(self, text: str) -> str:
        match = self.STOCK_CODE_RE.search(text or "")
        return match.group(0) if match else ""

    def _extract_stock_subject(self, text: str) -> Dict[str, str]:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return {}
        best = None
        best_len = -1
        for record in SubjectCatalog.list_by_type("stock"):
            subject_name = str(record.subject_name or "").strip()
            if not subject_name or subject_name not in normalized_text:
                continue
            if len(subject_name) > best_len:
                best = record
                best_len = len(subject_name)
        if not best:
            return {}
        return {
            "code": str(best.subject_code or "").split(".", 1)[0],
            "name": str(best.subject_name or "").strip(),
        }
