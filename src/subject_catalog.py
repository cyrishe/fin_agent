from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


STOCK_NAME_TSV = Path("stock_name.tsv")
INDEX_SUBJECTS_TSV = Path("data/index_subjects.tsv")


@dataclass(frozen=True)
class SubjectRecord:
    subject_type: str
    subject_code: str
    subject_name: str
    aliases: tuple[str, ...]


class SubjectCatalog:
    """
    Unified subject resolver for tools and indicator queries.

    Important:
    - `stock_name.tsv` remains stock-only, because it drives full-market stock batching.
    - index subjects are loaded from a separate file and merged only at resolver level.
    """

    _loaded = False
    _by_alias: Dict[str, SubjectRecord] = {}
    _by_code: Dict[str, SubjectRecord] = {}

    @classmethod
    def _register(cls, record: SubjectRecord) -> None:
        cls._by_code[record.subject_code] = record
        for alias in record.aliases:
            normalized = str(alias or "").strip()
            if normalized:
                cls._by_alias[normalized] = record
                cls._by_alias[normalized.lower()] = record

    @classmethod
    def _load_stock_subjects(cls) -> None:
        if not STOCK_NAME_TSV.exists():
            return
        for raw_line in STOCK_NAME_TSV.read_text(encoding="utf-8").splitlines():
            line = str(raw_line or "").strip()
            if not line or "\t" not in line:
                continue
            code, name = line.split("\t", 1)
            subject_code = str(code or "").strip().upper()
            subject_name = str(name or "").strip()
            code6 = subject_code.split(".", 1)[0]
            if not subject_code or not subject_name or not code6:
                continue
            cls._register(
                SubjectRecord(
                    subject_type="stock",
                    subject_code=subject_code,
                    subject_name=subject_name,
                    aliases=(subject_code, code6, subject_name),
                )
            )

    @classmethod
    def _load_index_subjects(cls) -> None:
        if not INDEX_SUBJECTS_TSV.exists():
            return
        for raw_line in INDEX_SUBJECTS_TSV.read_text(encoding="utf-8").splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            subject_code = str(parts[0] or "").strip().upper()
            subject_name = str(parts[1] or "").strip()
            subject_type = str(parts[2] or "").strip().lower() or "index"
            code6 = subject_code.split(".", 1)[0]
            aliases: List[str] = [subject_code, subject_name]
            if subject_code == "000001.SH":
                aliases.extend(["上证综指", "sh000001"])
            if subject_code == "399001.SZ":
                aliases.extend(["深证指数", "sz399001"])
            if code6:
                aliases.append(code6)
            cls._register(
                SubjectRecord(
                    subject_type=subject_type,
                    subject_code=subject_code,
                    subject_name=subject_name,
                    aliases=tuple(dict.fromkeys(aliases)),
                )
            )

    @classmethod
    def ensure_loaded(cls) -> None:
        if cls._loaded:
            return
        cls._loaded = True
        cls._by_alias = {}
        cls._by_code = {}
        cls._load_stock_subjects()
        cls._load_index_subjects()

    @classmethod
    def resolve(cls, value: str = "", *, subject_code: str = "", subject_name: str = "") -> Optional[SubjectRecord]:
        cls.ensure_loaded()
        candidates = [value, subject_code, subject_name]
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if not normalized:
                continue
            record = cls._by_alias.get(normalized) or cls._by_alias.get(normalized.lower()) or cls._by_code.get(normalized.upper())
            if record:
                return record
        return None

    @classmethod
    def list_by_type(cls, subject_type: str = "") -> List[SubjectRecord]:
        cls.ensure_loaded()
        normalized = str(subject_type or "").strip().lower()
        records = list(cls._by_code.values())
        if not normalized:
            return records
        return [record for record in records if record.subject_type == normalized]
