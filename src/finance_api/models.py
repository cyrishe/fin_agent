from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FinanceResponseMode = Literal["data", "summary", "both"]
FinanceRuntime = Literal["cc", "dsh"]
FinanceResearchMode = Literal["fast", "auto", "deep"]
FinanceExecutionMode = Literal["standard", "fast"]


class FinanceQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=4_000,
        description=(
            "Natural-language financial data question. It may request stock, index, "
            "industry, plate, fund, bond, hot-event, corporate disclosure, or research-report data."
        ),
        examples=["贵州茅台最近五个交易日的收盘价和涨跌幅是多少？"],
    )
    response_mode: FinanceResponseMode = Field(
        default="both",
        description=(
            "data returns structured source rows without a generated answer; summary returns only "
            "the generated answer; both returns the answer and structured source rows."
        ),
    )
    runtime: FinanceRuntime | None = Field(
        default=None,
        description="Optional execution runtime. Omit to use the server default.",
    )
    research_mode: FinanceResearchMode = Field(
        default="fast",
        description="Answer depth for summary generation. It does not change the underlying data contract.",
    )
    execution_mode: FinanceExecutionMode = Field(
        default="standard",
        description=(
            "DSH orchestration policy. fast performs only API discovery, call generation, "
            "and return, without agentic inspection, repair, retry, or result paging."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
        description=(
            "Stable caller-owned conversation identifier for multi-turn context. Omit for an isolated request."
        ),
    )
    max_rows: int = Field(
        default=100,
        ge=1,
        le=100,
        description="Maximum rows returned for each structured result in this response.",
    )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_execution_runtime(self) -> "FinanceQueryRequest":
        if self.runtime == "cc" and self.execution_mode == "fast":
            raise ValueError("execution_mode=fast 当前仅支持 runtime=dsh")
        return self


class FinanceAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4_000)
    runtime: FinanceRuntime | None = None
    research_mode: FinanceResearchMode = "fast"
    execution_mode: FinanceExecutionMode = "standard"
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    include_data: bool = Field(
        default=False,
        description="Include the structured evidence rows alongside the natural-language answer.",
    )
    max_rows: int = Field(default=100, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_execution_runtime(self) -> "FinanceAnswerRequest":
        if self.runtime == "cc" and self.execution_mode == "fast":
            raise ValueError("execution_mode=fast 当前仅支持 runtime=dsh")
        return self


class FinanceApiError(BaseModel):
    code: str
    message: str


class FinanceDataSource(BaseModel):
    """Business-facing provenance for one executed data goal."""

    label: str
    data_object: str
    data_type: str
    description: str = ""
    query_goal: str
    row_count: int = 0


class FinanceResultPage(BaseModel):
    result_name: str
    goal: str
    data_type: str = "table"
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    row_count: int = 0
    rows_returned: int = 0
    truncated: bool = False
    rows: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class FinanceDataPayload(BaseModel):
    format: str = "row-dict"
    results: list[FinanceResultPage] = Field(default_factory=list)


class FinanceExecutionMetadata(BaseModel):
    duration_ms: int = 0
    worker_index: int | None = None
    queue_wait_ms: int = 0
    model_name: str = ""
    reasoning_effort: str = ""
    tool_call_count: int = 0
    result_count: int = 0
    total_rows: int = 0
    returned_rows: int = 0
    truncated: bool = False


class FinanceQueryResponse(BaseModel):
    id: str
    object: Literal["finance.query"] = "finance.query"
    created_at: str
    ok: bool
    query: str
    response_mode: FinanceResponseMode
    runtime: FinanceRuntime
    execution_mode: FinanceExecutionMode = "standard"
    conversation_id: str | None = None
    data_sources: list[FinanceDataSource] = Field(default_factory=list)
    summary: str | None = None
    data: FinanceDataPayload | None = None
    execution: FinanceExecutionMetadata
    error: FinanceApiError | None = None
