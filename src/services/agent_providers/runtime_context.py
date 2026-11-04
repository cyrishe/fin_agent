from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Mapping
import uuid


def _trim(value: Any) -> str:
    return str(value or "").strip()


class AgentRuntimeContextAdapter:
    """Own provider session context outside business workflow state."""

    def __init__(self, root_dir: str | Path = "data/agent_runtime_context") -> None:
        self.root_dir = Path(root_dir)
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def invoke(
        self,
        *,
        scope_id: str,
        role: str,
        runner: Callable[..., Dict[str, Any]],
        state: Mapping[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        scope = _trim(scope_id)
        role_name = _trim(role)
        if not scope or not role_name:
            return runner(state=dict(state), **kwargs)
        lock_key = f"{scope}:{role_name}"
        with self._lock(lock_key), self._file_lock(scope):
            document = self._load(scope)
            roles = document.setdefault("roles", {})
            runtime = dict(roles.get(role_name) or {})
            runtime.setdefault("session_id", uuid.uuid4().hex)
            # The logical session owns the workspace and must survive failures
            # that happen after the delegated provider has already done work.
            roles[role_name] = dict(runtime)
            self._save(scope, document)

            runtime_state = dict(state)
            runtime_state["agent_runtime"] = dict(runtime)
            result = runner(state=runtime_state, **kwargs)
            normalized = dict(result or {})
            result_state = (
                dict(normalized.get("state") or {})
                if isinstance(normalized.get("state"), Mapping)
                else {}
            )
            returned_runtime = (
                dict(result_state.pop("agent_runtime") or {})
                if isinstance(result_state.get("agent_runtime"), Mapping)
                else {}
            )
            implementation_meta = (
                normalized.get("implementation_meta")
                if isinstance(normalized.get("implementation_meta"), Mapping)
                else {}
            )
            runtime.update(returned_runtime)
            provider_session_id = _trim(implementation_meta.get("provider_session_id"))
            if provider_session_id:
                runtime["provider_session_id"] = provider_session_id
            roles[role_name] = runtime
            self._save(scope, document)

            normalized["state"] = result_state
            patch = (
                dict(normalized.get("thread_context_patch") or {})
                if isinstance(normalized.get("thread_context_patch"), Mapping)
                else {}
            )
            if isinstance(patch.get("custom_tool_state"), Mapping):
                clean_patch_state = dict(patch["custom_tool_state"])
                clean_patch_state.pop("agent_runtime", None)
                patch["custom_tool_state"] = clean_patch_state
                normalized["thread_context_patch"] = patch
            return normalized

    def read(self, *, scope_id: str, role: str) -> Dict[str, Any]:
        with self._lock(f"{scope_id}:{role}"), self._file_lock(scope_id):
            return dict((self._load(scope_id).get("roles") or {}).get(role) or {})

    def _load(self, scope_id: str) -> Dict[str, Any]:
        path = self._path(scope_id)
        if not path.is_file():
            return {"roles": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"roles": {}}
        return value if isinstance(value, dict) else {"roles": {}}

    def _save(self, scope_id: str, document: Mapping[str, Any]) -> None:
        path = self._path(scope_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _path(self, scope_id: str) -> Path:
        digest = hashlib.sha256(_trim(scope_id).encode("utf-8")).hexdigest()[:32]
        return self.root_dir / f"{digest}.json"

    @contextmanager
    def _file_lock(self, scope_id: str):
        path = self._path(scope_id).with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _lock(self, key: str):
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            yield
