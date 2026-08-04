from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Mapping

from src.services.scheduled_task_compiler import ScheduledTaskCompiler
from src.services.scheduled_task_store import MySqlScheduledTaskStore, ScheduledTaskStore


class ScheduledTaskNotFoundError(LookupError):
    pass


class ScheduledTaskService:
    def __init__(
        self,
        *,
        store: ScheduledTaskStore | None = None,
        compiler: ScheduledTaskCompiler | None = None,
    ) -> None:
        self.store = store or MySqlScheduledTaskStore()
        self.compiler = compiler or ScheduledTaskCompiler()

    def preview(
        self,
        *,
        owner_user_id: str,
        instruction: str,
        draft: Mapping[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any]:
        owner = self._owner(owner_user_id)
        return _public(
            self.compiler.compile(
                instruction=instruction,
                owner_user_id=owner,
                draft=draft,
                now=now,
            )
        )

    def create(
        self,
        *,
        owner_user_id: str,
        instruction: str,
        draft: Mapping[str, Any] | None = None,
        idempotency_key: str = "",
        now: dt.datetime | None = None,
    ) -> Dict[str, Any]:
        owner = self._owner(owner_user_id)
        compiled = self.compiler.compile(
            instruction=instruction,
            owner_user_id=owner,
            draft=draft,
            now=now,
        )
        created = self.store.create(
            owner_user_id=owner,
            draft=compiled,
            idempotency_key=idempotency_key,
        )
        return _public(created)

    def list(self, *, owner_user_id: str) -> list[Dict[str, Any]]:
        owner = self._owner(owner_user_id)
        return _public(self.store.list_for_owner(owner_user_id=owner))

    def get(self, *, owner_user_id: str, schedule_id: str) -> Dict[str, Any]:
        item = self.store.get_for_owner(
            owner_user_id=self._owner(owner_user_id),
            schedule_id=self._schedule_id(schedule_id),
        )
        if not item:
            raise ScheduledTaskNotFoundError("定时任务不存在")
        return _public(item)

    def update(
        self,
        *,
        owner_user_id: str,
        schedule_id: str,
        instruction: str = "",
        draft: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any]:
        owner = self._owner(owner_user_id)
        normalized_id = self._schedule_id(schedule_id)
        compiled = None
        if str(instruction or "").strip() or draft is not None:
            current = self.store.get_for_owner(
                schedule_id=normalized_id,
                owner_user_id=owner,
            )
            if not current:
                raise ScheduledTaskNotFoundError("定时任务不存在")
            compiled = self.compiler.compile(
                instruction=str(instruction or "").strip() or current["requirement_brief"],
                owner_user_id=owner,
                draft=draft,
                now=now,
            )
        if compiled is None and enabled is None:
            raise ValueError("至少需要提供 instruction、draft 或 enabled")
        updated = self.store.update(
            schedule_id=normalized_id,
            owner_user_id=owner,
            draft=compiled,
            enabled=enabled,
            now=now,
        )
        if not updated:
            raise ScheduledTaskNotFoundError("定时任务不存在")
        return _public(updated)

    def run_now(
        self,
        *,
        owner_user_id: str,
        schedule_id: str,
        now: dt.datetime | None = None,
    ) -> Dict[str, Any]:
        run = self.store.enqueue_manual(
            owner_user_id=self._owner(owner_user_id),
            schedule_id=self._schedule_id(schedule_id),
            now=now,
        )
        if not run:
            raise ScheduledTaskNotFoundError("定时任务不存在")
        return _public(run)

    def list_runs(
        self,
        *,
        owner_user_id: str,
        schedule_id: str,
        limit: int = 50,
    ) -> list[Dict[str, Any]]:
        owner = self._owner(owner_user_id)
        normalized_id = self._schedule_id(schedule_id)
        if not self.store.get_for_owner(
            schedule_id=normalized_id,
            owner_user_id=owner,
        ):
            raise ScheduledTaskNotFoundError("定时任务不存在")
        return _public(
            self.store.list_runs_for_owner(
                schedule_id=normalized_id,
                owner_user_id=owner,
                limit=limit,
            )
        )

    def get_run(self, *, owner_user_id: str, run_id: str) -> Dict[str, Any]:
        run = self.store.get_run_for_owner(
            owner_user_id=self._owner(owner_user_id),
            run_id=str(run_id or "").strip(),
        )
        if not run:
            raise ScheduledTaskNotFoundError("定时任务运行不存在")
        return _public(run)

    @staticmethod
    def _owner(value: str) -> str:
        owner = str(value or "").strip()
        if not owner:
            raise ValueError("缺少当前用户身份")
        return owner

    @staticmethod
    def _schedule_id(value: str) -> str:
        schedule_id = str(value or "").strip()
        if not schedule_id:
            raise ValueError("schedule_id 不能为空")
        return schedule_id


def _public(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value
