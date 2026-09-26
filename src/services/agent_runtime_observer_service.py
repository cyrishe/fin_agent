from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.services.prompt_context_compiler_service import PromptContextCompilerService
from src.utils.ai_service import chat_qwen_json


class AgentRuntimeObserverService:
    def __init__(self, *, prompt_context_compiler: Optional[PromptContextCompilerService] = None) -> None:
        self.registry = get_prompt_registry()
        self.prompt_context_compiler = prompt_context_compiler or PromptContextCompilerService()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def observe(
        self,
        *,
        user_objective: str,
        execution_plan: Optional[Dict[str, Any]] = None,
        evidence_state: Optional[Dict[str, Any]] = None,
        completed_items: Optional[List[Dict[str, Any]]] = None,
        enable_llm: bool = True,
    ) -> Dict[str, Any]:
        plan = execution_plan if isinstance(execution_plan, dict) else {}
        evidence = evidence_state if isinstance(evidence_state, dict) else {}
        completed = completed_items if isinstance(completed_items, list) else []
        prompt_context_sections = self.prompt_context_compiler.compile_sections(
            profile="observer",
            execution_plan=plan,
            output_contract=evidence.get("output_contract") if isinstance(evidence.get("output_contract"), dict) else {},
        )

        if not enable_llm:
            return {
                "ok": True,
                "source": "observer_disabled",
                "observation": self._fallback_observation(plan=plan, evidence=evidence, completed=completed),
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        try:
            messages = self.registry.render_messages(
                "system.agent_runtime.observer",
                {
                    "user_objective": self._trim(user_objective),
                    "prompt_context_sections": prompt_context_sections,
                    "execution_plan": plan,
                    "evidence_state": evidence,
                    "completed_items": completed,
                },
            )
            payload, usage = chat_qwen_json(messages, enable_think=False)
            if isinstance(payload, dict):
                return {
                    "ok": True,
                    "source": "llm_observer",
                    "observation": self._normalize_observation(payload),
                    "raw_observation": payload,
                    "llm_usage": self._normalize_usage(usage),
                }
        except Exception as exc:
            return {
                "ok": False,
                "source": "observer_error_fallback",
                "error": str(exc),
                "observation": self._fallback_observation(plan=plan, evidence=evidence, completed=completed),
                "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        return {
            "ok": True,
            "source": "observer_empty_fallback",
            "observation": self._fallback_observation(plan=plan, evidence=evidence, completed=completed),
            "llm_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _normalize_observation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        revision_patch = payload.get("revision_patch") if isinstance(payload.get("revision_patch"), dict) else {}
        return {
            "plan_still_valid": bool(payload.get("plan_still_valid", True)),
            "goal_progress": self._trim(payload.get("goal_progress")) or "partial",
            "recommended_action": self._trim(payload.get("recommended_action")) or "continue",
            "missing_evidence": [self._trim(item) for item in (payload.get("missing_evidence") or []) if self._trim(item)],
            "revision_patch": {
                "append_tools": [self._trim(item) for item in (revision_patch.get("append_tools") or []) if self._trim(item)],
                "notes": [self._trim(item) for item in (revision_patch.get("notes") or []) if self._trim(item)],
            },
            "reason": self._trim(payload.get("reason")),
        }

    def _fallback_observation(self, *, plan: Dict[str, Any], evidence: Dict[str, Any], completed: List[Dict[str, Any]]) -> Dict[str, Any]:
        completed_count = len(completed)
        selected_tools = evidence.get("selected_tools") if isinstance(evidence.get("selected_tools"), list) else []
        if completed_count > 0 or selected_tools:
            return {
                "plan_still_valid": True,
                "goal_progress": "partial",
                "recommended_action": "continue",
                "missing_evidence": [],
                "revision_patch": {"append_tools": [], "notes": []},
                "reason": "已有执行证据，先继续当前计划。",
            }
        return {
            "plan_still_valid": True,
            "goal_progress": "none",
            "recommended_action": "continue",
            "missing_evidence": [],
            "revision_patch": {"append_tools": [], "notes": []},
            "reason": self._trim(plan.get("reason")) or "先按当前计划执行。",
        }

    def _normalize_usage(self, usage: Any) -> Dict[str, int]:
        if isinstance(usage, dict):
            return {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
            }
        return {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }
