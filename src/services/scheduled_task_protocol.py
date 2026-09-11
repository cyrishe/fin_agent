from __future__ import annotations

import datetime as dt
import re
from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduledTaskProtocolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CronExpression:
    """Small five-field cron implementation for persisted server schedules."""

    _FIELD_RANGES = (
        (0, 59),
        (0, 23),
        (1, 31),
        (1, 12),
        (0, 7),
    )

    def __init__(self, expression: str) -> None:
        self.expression = " ".join(str(expression or "").strip().split())
        fields = self.expression.split(" ")
        if len(fields) != 5:
            raise ScheduledTaskProtocolError(
                "invalid_cron",
                "cron 必须是 minute hour day-of-month month day-of-week 五段表达式",
            )
        parsed = [
            self._parse_field(field, minimum=limits[0], maximum=limits[1])
            for field, limits in zip(fields, self._FIELD_RANGES)
        ]
        self.minutes, self.hours, self.month_days, self.months, raw_weekdays = parsed
        self.weekdays = {0 if value == 7 else value for value in raw_weekdays}
        self.month_day_is_wildcard = fields[2].startswith("*")
        self.weekday_is_wildcard = fields[4].startswith("*")

    def next_after(self, after: dt.datetime, *, timezone: str) -> dt.datetime:
        zone = parse_timezone(timezone)
        current = ensure_utc(after).astimezone(zone)
        candidate = current.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        max_minutes = 60 * 24 * 366 * 2
        for _ in range(max_minutes):
            if self.matches(candidate) and self._is_valid_local_time(candidate, zone):
                return candidate.astimezone(dt.timezone.utc)
            candidate += dt.timedelta(minutes=1)
        raise ScheduledTaskProtocolError(
            "cron_out_of_range",
            "未来两年内没有找到下一次执行时间",
        )

    def matches(self, value: dt.datetime) -> bool:
        cron_weekday = (value.weekday() + 1) % 7
        month_day_match = value.day in self.month_days
        weekday_match = cron_weekday in self.weekdays
        if self.month_day_is_wildcard and self.weekday_is_wildcard:
            day_match = True
        elif self.month_day_is_wildcard:
            day_match = weekday_match
        elif self.weekday_is_wildcard:
            day_match = month_day_match
        else:
            day_match = month_day_match or weekday_match
        return (
            value.minute in self.minutes
            and value.hour in self.hours
            and value.month in self.months
            and day_match
        )

    @staticmethod
    def _is_valid_local_time(value: dt.datetime, zone: ZoneInfo) -> bool:
        round_trip = value.astimezone(dt.timezone.utc).astimezone(zone)
        return (
            value.replace(fold=round_trip.fold)
            == round_trip
        )

    @staticmethod
    def _parse_field(field: str, *, minimum: int, maximum: int) -> set[int]:
        values: set[int] = set()
        for part in str(field or "").split(","):
            token = part.strip()
            if not token:
                raise ScheduledTaskProtocolError("invalid_cron", "cron 字段不能为空")
            step = 1
            if "/" in token:
                base, raw_step = token.split("/", 1)
                try:
                    step = int(raw_step)
                except Exception as exc:
                    raise ScheduledTaskProtocolError("invalid_cron", "cron 步长必须是整数") from exc
                if step <= 0:
                    raise ScheduledTaskProtocolError("invalid_cron", "cron 步长必须大于 0")
            else:
                base = token
            if base == "*":
                start, end = minimum, maximum
            elif "-" in base:
                raw_start, raw_end = base.split("-", 1)
                try:
                    start, end = int(raw_start), int(raw_end)
                except Exception as exc:
                    raise ScheduledTaskProtocolError("invalid_cron", "cron 范围必须是整数") from exc
            else:
                try:
                    start = int(base)
                    end = maximum if "/" in token else start
                except Exception as exc:
                    raise ScheduledTaskProtocolError("invalid_cron", "cron 字段必须是整数、范围或通配符") from exc
            if start < minimum or end > maximum or start > end:
                raise ScheduledTaskProtocolError(
                    "invalid_cron",
                    f"cron 字段范围必须在 {minimum}..{maximum}",
                )
            values.update(range(start, end + 1, step))
        if not values:
            raise ScheduledTaskProtocolError("invalid_cron", "cron 字段没有可用值")
        return values


def ensure_utc(value: dt.datetime | None) -> dt.datetime:
    resolved = value or dt.datetime.now(dt.timezone.utc)
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=dt.timezone.utc)
    return resolved.astimezone(dt.timezone.utc)


def parse_timezone(value: str) -> ZoneInfo:
    normalized = str(value or "").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ScheduledTaskProtocolError(
            "invalid_timezone",
            f"未知时区：{normalized}",
        ) from exc


def normalize_schedule_draft(
    payload: Mapping[str, Any],
    *,
    now: dt.datetime | None = None,
) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ScheduledTaskProtocolError("invalid_schedule", "定时任务草稿必须是对象")
    requirement_brief = str(
        payload.get("requirement_brief")
        or payload.get("instruction")
        or ""
    ).strip()
    if not requirement_brief:
        raise ScheduledTaskProtocolError("missing_instruction", "缺少定时任务说明")
    trigger = payload.get("trigger") if isinstance(payload.get("trigger"), Mapping) else {}
    timezone = str(trigger.get("timezone") or payload.get("timezone") or "Asia/Shanghai").strip()
    cron = str(trigger.get("cron") or payload.get("cron") or "").strip()
    parse_timezone(timezone)
    cron_expression = CronExpression(cron)
    execution_plan = normalize_execution_plan(
        payload.get("execution_plan")
        if isinstance(payload.get("execution_plan"), Mapping)
        else {"steps": payload.get("steps")}
    )
    current = ensure_utc(now)
    return {
        "schema_version": "scheduled_task.v1",
        "requirement_brief": requirement_brief,
        "trigger": {
            "cron": cron_expression.expression,
            "timezone": timezone,
        },
        "execution_plan": execution_plan,
        "next_run_at": cron_expression.next_after(current, timezone=timezone),
    }


def normalize_execution_plan(payload: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ScheduledTaskProtocolError("invalid_execution_plan", "execution_plan 必须是对象")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScheduledTaskProtocolError("missing_steps", "定时任务至少需要一个执行步骤")
    if len(raw_steps) > 12:
        raise ScheduledTaskProtocolError("too_many_steps", "定时任务最多支持 12 个执行步骤")

    steps: list[Dict[str, Any]] = []
    step_ids: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, Mapping):
            raise ScheduledTaskProtocolError("invalid_step", f"第 {index + 1} 个步骤必须是对象")
        step_id = str(raw_step.get("step_id") or f"step_{index + 1}").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", step_id):
            raise ScheduledTaskProtocolError("invalid_step_id", f"无效 step_id：{step_id}")
        if step_id in step_ids:
            raise ScheduledTaskProtocolError("duplicate_step_id", f"重复 step_id：{step_id}")
        step_ids.add(step_id)
        target = raw_step.get("target_ref") if isinstance(raw_step.get("target_ref"), Mapping) else {}
        step_type = str(raw_step.get("type") or target.get("kind") or "").strip().lower()
        if step_type not in {"tool", "skill"}:
            raise ScheduledTaskProtocolError(
                "unsupported_step_type",
                f"步骤 {step_id} 只支持 tool 或 skill",
            )
        target_name = str(
            target.get("name")
            or raw_step.get("target_name")
            or raw_step.get("name")
            or ""
        ).strip()
        if not target_name:
            raise ScheduledTaskProtocolError("missing_target", f"步骤 {step_id} 缺少目标资产")
        inputs = raw_step.get("inputs")
        if inputs is None:
            inputs = raw_step.get("arguments")
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, Mapping):
            raise ScheduledTaskProtocolError("invalid_step_inputs", f"步骤 {step_id} 的 inputs 必须是对象")
        depends_on = _unique_strings(raw_step.get("depends_on") or [])
        steps.append(
            {
                "step_id": step_id,
                "type": step_type,
                "target_ref": {
                    "kind": step_type,
                    "name": target_name,
                    "version": str(target.get("version") or raw_step.get("version") or "v1").strip() or "v1",
                    **(
                        {"revision": int(target.get("revision"))}
                        if str(target.get("revision") or "").isdigit()
                        else {}
                    ),
                },
                "inputs": deepcopy(dict(inputs)),
                "depends_on": depends_on,
            }
        )

    known_ids = {step["step_id"] for step in steps}
    for step in steps:
        missing = [item for item in step["depends_on"] if item not in known_ids]
        if missing:
            raise ScheduledTaskProtocolError(
                "unknown_dependency",
                f"步骤 {step['step_id']} 依赖不存在的步骤 {missing[0]}",
            )
        if step["step_id"] in step["depends_on"]:
            raise ScheduledTaskProtocolError(
                "cyclic_dependency",
                f"步骤 {step['step_id']} 不能依赖自身",
            )
        _validate_result_bindings(
            step["inputs"],
            step_id=step["step_id"],
            dependencies=set(step["depends_on"]),
        )
    _assert_acyclic(steps)
    return {
        "schema_version": "scheduled_execution_plan.v1",
        "steps": steps,
    }


def _unique_strings(values: Iterable[Any]) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        raise ScheduledTaskProtocolError("invalid_dependencies", "depends_on 必须是数组")
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _assert_acyclic(steps: list[Dict[str, Any]]) -> None:
    dependencies = {
        step["step_id"]: set(step["depends_on"])
        for step in steps
    }
    completed: set[str] = set()
    while len(completed) < len(steps):
        ready = [
            step_id
            for step_id, required in dependencies.items()
            if step_id not in completed and required <= completed
        ]
        if not ready:
            raise ScheduledTaskProtocolError("cyclic_dependency", "执行步骤存在循环依赖")
        completed.update(ready)


def _validate_result_bindings(
    value: Any,
    *,
    step_id: str,
    dependencies: set[str],
) -> None:
    if isinstance(value, Mapping):
        if set(value.keys()) == {"$from"}:
            reference = str(value.get("$from") or "").strip()
            parts = reference.split(".")
            if len(parts) < 2 or parts[1] != "result":
                raise ScheduledTaskProtocolError(
                    "invalid_result_reference",
                    f"步骤 {step_id} 的 $from 必须是 <step_id>.result[.path] 格式",
                )
            if parts[0] not in dependencies:
                raise ScheduledTaskProtocolError(
                    "undeclared_result_dependency",
                    f"步骤 {step_id} 引用了未声明依赖的步骤 {parts[0]}",
                )
            return
        for nested in value.values():
            _validate_result_bindings(
                nested,
                step_id=step_id,
                dependencies=dependencies,
            )
        return
    if isinstance(value, list):
        for nested in value:
            _validate_result_bindings(
                nested,
                step_id=step_id,
                dependencies=dependencies,
            )
