from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


_PROVIDER_RETRY_STATUSES = frozenset(
    {
        "provider_error",
        "provider_exception",
        "rate_limited",
        "timeout",
    }
)


def provider_retry_allowed(
    execution: Mapping[str, Any] | None,
    *,
    retries_used: int,
) -> bool:
    """Allow one exact, model-free retry for a provider execution failure."""

    payload = execution if isinstance(execution, Mapping) else {}
    status = str(payload.get("status") or "").strip().lower()
    return (
        bool(payload)
        and not bool(payload.get("ok"))
        and status in _PROVIDER_RETRY_STATUSES
        and retries_used < 1
    )


def query_recovery(
    *,
    validation: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    result_data: Mapping[str, Any] | None = None,
    provider_retries_used: int = 0,
) -> Optional[Dict[str, Any]]:
    """Describe the narrow recovery action without inventing business state.

    The object is an execution diagnostic for the current tool result. It does
    not become a conversation state machine. In particular, a successful empty
    result is complete unless the provider itself supplied an ambiguity that
    can be resolved from explicit candidates.
    """

    validation_payload = validation if isinstance(validation, Mapping) else {}
    if validation_payload and not bool(validation_payload.get("ok")):
        return {
            "category": "request_invalid",
            "retryable": True,
            "owner": "cc",
            "max_retries": 1,
            "guidance": (
                "只依据已读取的数据目录修正失败请求一次；保留已经成功的步骤，"
                "不得通过更换业务目标或删除用户条件来碰运气。"
            ),
        }

    execution_payload = execution if isinstance(execution, Mapping) else {}
    if execution_payload and not bool(execution_payload.get("ok")):
        retries_used = max(0, int(provider_retries_used))
        guidance = (
            "Harness 已对可恢复的数据源失败使用原请求自动重试一次；"
            "仍失败时停止本证据目标，不改字段、日期或过滤条件继续试错。"
            if retries_used
            else (
                "运行时未把该失败声明为可恢复；停止本证据目标，"
                "不通过改字段、日期或过滤条件继续试错。"
            )
        )
        return {
            "category": "provider_failure",
            "retryable": False,
            "owner": "harness",
            "automatic_retries_used": retries_used,
            "guidance": guidance,
        }

    data = result_data if isinstance(result_data, Mapping) else {}
    resolution = (
        data.get("name_resolution")
        if isinstance(data.get("name_resolution"), Mapping)
        else {}
    )
    if (
        str(resolution.get("status") or "").strip().lower() == "ambiguous"
        and isinstance(resolution.get("candidates"), list)
        and resolution.get("candidates")
    ):
        return {
            "category": "ambiguous_identity",
            "retryable": True,
            "owner": "cc",
            "max_retries": 1,
            "candidates": list(resolution.get("candidates") or [])[:6],
            "guidance": (
                "工具已完成一次受控候选解析。只在会话语义能唯一确定候选时"
                "使用候选的精确身份重试一次；仍有歧义时询问用户，不再扩大模糊范围。"
            ),
        }

    try:
        row_count = int(data.get("row_count") or len(data.get("rows") or []))
    except (TypeError, ValueError):
        row_count = 0
    if row_count == 0:
        return {
            "category": "empty_success",
            "retryable": False,
            "owner": "tool",
            "guidance": (
                "请求已按当前对象、指标、时间和过滤口径成功执行但没有记录；"
                "把它作为证据边界，不自动放宽名称、日期或用户条件。"
            ),
        }
    return None
