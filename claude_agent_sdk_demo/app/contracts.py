from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=20_000)
    session_id: str | None = None
    skill_names: list[str] = Field(default_factory=lambda: ["financial-research"], max_length=8)
    enable_web_search: bool = True
    output_mode: Literal["text", "research_json"] = "text"
    client_request_id: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not SESSION_ID_PATTERN.fullmatch(value):
            raise ValueError("session_id contains unsupported characters")
        return value

    @field_validator("skill_names")
    @classmethod
    def validate_skills(cls, value: list[str]) -> list[str]:
        unique: list[str] = []
        for name in value:
            normalized = name.strip().lower()
            if not SKILL_NAME_PATTERN.fullmatch(normalized):
                raise ValueError(f"invalid skill name: {name}")
            if normalized not in unique:
                unique.append(normalized)
        return unique

    @model_validator(mode="after")
    def validate_metadata_size(self) -> "RunRequest":
        encoded = json.dumps(self.metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > 4_096:
            raise ValueError("metadata is too large")
        return self


class BackendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    question: str
    session_id: str | None = None
    skill_names: list[str] = Field(default_factory=list)
    enable_web_search: bool = True
    output_mode: Literal["text", "research_json"] = "text"
    client_request_id: str | None = None


RESEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string"},
                    "as_of": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["statement", "as_of", "source_url"],
                "additionalProperties": False,
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "facts", "uncertainties"],
    "additionalProperties": False,
}
