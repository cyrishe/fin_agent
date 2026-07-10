from __future__ import annotations

from typing import Any, Dict, List, Optional


class PromptContextCompilerService:
    DEFAULT_BUDGETS = {
        "agent_role_section": 400,
        "interaction_mode_section": 120,
        "current_focus_section": 220,
        "thread_summary_section": 600,
        "active_task_section": 260,
        "relevant_objects_section": 400,
        "candidate_skills_section": 500,
        "candidate_tools_section": 500,
        "runtime_constraints_section": 220,
        "output_contract_section": 260,
    }

    PROFILE_BUDGETS = {
        "planner": {
            "current_focus_section": {"priority": 100, "token_budget": 260},
            "active_task_section": {"priority": 95, "token_budget": 320},
            "candidate_skills_section": {"priority": 90, "token_budget": 650},
            "candidate_tools_section": {"priority": 90, "token_budget": 650},
            "thread_summary_section": {"priority": 80, "token_budget": 500},
        },
        "observer": {
            "active_task_section": {"priority": 100, "token_budget": 360},
            "thread_summary_section": {"priority": 95, "token_budget": 500},
            "relevant_objects_section": {"priority": 90, "token_budget": 420},
            "runtime_constraints_section": {"priority": 85, "token_budget": 260},
        },
        "synthesis": {
            "thread_summary_section": {"priority": 100, "token_budget": 700},
            "active_task_section": {"priority": 95, "token_budget": 320},
            "relevant_objects_section": {"priority": 90, "token_budget": 450},
            "output_contract_section": {"priority": 100, "token_budget": 340},
        },
    }

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def compile_sections(
        self,
        *,
        profile: str = "planner",
        agent_context: Optional[Dict[str, Any]] = None,
        interaction_frame: Optional[Dict[str, Any]] = None,
        conversation_state: Optional[Dict[str, Any]] = None,
        work_context: Optional[Dict[str, Any]] = None,
        execution_plan: Optional[Dict[str, Any]] = None,
        candidate_skills: Optional[List[Dict[str, Any]]] = None,
        candidate_tools: Optional[List[Dict[str, Any]]] = None,
        output_contract: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        agent_ctx = agent_context if isinstance(agent_context, dict) else {}
        frame = interaction_frame if isinstance(interaction_frame, dict) else {}
        state = conversation_state if isinstance(conversation_state, dict) else {}
        work = work_context if isinstance(work_context, dict) else {}
        plan = execution_plan if isinstance(execution_plan, dict) else {}
        contract = output_contract if isinstance(output_contract, dict) else {}
        profile_name = self._trim(profile) or "planner"
        profile_budgets = self.PROFILE_BUDGETS.get(profile_name, {})
        sections = {
            "agent_role_section": {
                "required": True,
                "priority": self._budget_value(profile_budgets, "agent_role_section", "priority", 70),
                "token_budget": self._budget_value(profile_budgets, "agent_role_section", "token_budget", self.DEFAULT_BUDGETS["agent_role_section"]),
                "content": {
                    "assistant_agent": self._trim(agent_ctx.get("assistant_agent")),
                    "execution_agent": self._trim(agent_ctx.get("execution_agent")),
                    "planner_agent": self._trim(plan.get("planner_agent")),
                },
            },
            "interaction_mode_section": {
                "required": True,
                "priority": self._budget_value(profile_budgets, "interaction_mode_section", "priority", 75),
                "token_budget": self._budget_value(profile_budgets, "interaction_mode_section", "token_budget", self.DEFAULT_BUDGETS["interaction_mode_section"]),
                "content": {
                    "interaction_mode": self._trim(frame.get("interaction_mode")),
                    "conversation_state": self._trim(state.get("state")),
                },
            },
            "current_focus_section": {
                "required": True,
                "priority": self._budget_value(profile_budgets, "current_focus_section", "priority", 80),
                "token_budget": self._budget_value(profile_budgets, "current_focus_section", "token_budget", self.DEFAULT_BUDGETS["current_focus_section"]),
                "content": {
                    "active_focus_type": self._trim(frame.get("active_focus_type")),
                    "active_focus_id": self._trim(frame.get("active_focus_id")),
                    "reference_scope": frame.get("reference_scope") if isinstance(frame.get("reference_scope"), list) else [],
                },
            },
            "thread_summary_section": {
                "required": False,
                "priority": self._budget_value(profile_budgets, "thread_summary_section", "priority", 60),
                "token_budget": self._budget_value(profile_budgets, "thread_summary_section", "token_budget", self.DEFAULT_BUDGETS["thread_summary_section"]),
                "content": {
                    "current_user_goal": self._trim(frame.get("current_user_goal")),
                    "recent_result_subject": self._trim(work.get("recent_result_subject")),
                },
            },
            "active_task_section": {
                "required": False,
                "priority": self._budget_value(profile_budgets, "active_task_section", "priority", 65),
                "token_budget": self._budget_value(profile_budgets, "active_task_section", "token_budget", self.DEFAULT_BUDGETS["active_task_section"]),
                "content": {
                    "recent_task_type": self._trim(work.get("recent_task_type")),
                    "plan_type": self._trim(plan.get("plan_type")),
                    "selected_skill": self._trim(plan.get("selected_skill")),
                },
            },
            "relevant_objects_section": {
                "required": False,
                "priority": self._budget_value(profile_budgets, "relevant_objects_section", "priority", 55),
                "token_budget": self._budget_value(profile_budgets, "relevant_objects_section", "token_budget", self.DEFAULT_BUDGETS["relevant_objects_section"]),
                "content": {
                    "active_skill_name": self._trim(work.get("active_skill_canonical_name") or work.get("active_skill_name")),
                    "active_agent_name": self._trim(work.get("active_agent_name")),
                    "last_image_type": self._trim(work.get("last_image_type")),
                    "last_visual_subjects": work.get("last_visual_subjects") if isinstance(work.get("last_visual_subjects"), list) else [],
                },
            },
            "candidate_skills_section": {
                "required": False,
                "priority": self._budget_value(profile_budgets, "candidate_skills_section", "priority", 50),
                "token_budget": self._budget_value(profile_budgets, "candidate_skills_section", "token_budget", self.DEFAULT_BUDGETS["candidate_skills_section"]),
                "content": candidate_skills or [],
            },
            "candidate_tools_section": {
                "required": False,
                "priority": self._budget_value(profile_budgets, "candidate_tools_section", "priority", 50),
                "token_budget": self._budget_value(profile_budgets, "candidate_tools_section", "token_budget", self.DEFAULT_BUDGETS["candidate_tools_section"]),
                "content": candidate_tools or [],
            },
            "runtime_constraints_section": {
                "required": True,
                "priority": self._budget_value(profile_budgets, "runtime_constraints_section", "priority", 70),
                "token_budget": self._budget_value(profile_budgets, "runtime_constraints_section", "token_budget", self.DEFAULT_BUDGETS["runtime_constraints_section"]),
                "content": {
                    "accepted_constraints": frame.get("accepted_constraints") if isinstance(frame.get("accepted_constraints"), list) else [],
                    "pending_questions": frame.get("pending_questions") if isinstance(frame.get("pending_questions"), list) else [],
                },
            },
            "output_contract_section": {
                "required": False,
                "priority": self._budget_value(profile_budgets, "output_contract_section", "priority", 65),
                "token_budget": self._budget_value(profile_budgets, "output_contract_section", "token_budget", self.DEFAULT_BUDGETS["output_contract_section"]),
                "content": contract,
            },
        }
        return {
            "compiler_type": "prompt_context_compiler",
            "profile": profile_name,
            "sections": sections,
        }

    def _budget_value(self, profile_budgets: Dict[str, Dict[str, int]], section_name: str, key: str, default: int) -> int:
        section = profile_budgets.get(section_name)
        if isinstance(section, dict):
            return int(section.get(key, default))
        return int(default)
