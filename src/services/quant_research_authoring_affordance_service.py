from __future__ import annotations

from typing import Any, Dict, List, Mapping


class QuantResearchAuthoringAffordanceService:
    """Builds read-only affordances for quant research authoring.

    This service does not interpret business concepts, create specs, call LLMs,
    run SQL, or execute strategies. It only exposes the governed draft lanes
    that the planner/output layer may present to the user.
    """

    _QUANT_TERMS = [
        "股票",
        "选股",
        "策略",
        "因子",
        "回测",
        "动量",
        "市值",
        "成长性",
        "资金流",
        "研报",
        "基金",
        "指数",
        "板块",
    ]
    _SQL_TEMPLATE_TERMS = ["sql", "chatbi", "查询模板", "sql模板", "模板", "固化查询"]

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _contains_any(cls, text: str, keywords: List[str]) -> bool:
        normalized = cls._trim(text).lower()
        return any(cls._trim(keyword).lower() in normalized for keyword in keywords)

    def build_affordance(
        self,
        *,
        objective: str,
        planner_question_contract: Mapping[str, Any],
    ) -> Dict[str, Any]:
        text = self._trim(objective)
        lanes = self._lane_names(planner_question_contract)
        has_authoring_lane = bool({"skill_lifecycle", "code_execution"} & lanes)
        has_quant_language = self._contains_any(text, self._QUANT_TERMS)
        available = bool(text and has_authoring_lane and has_quant_language)
        draft_targets = self._draft_targets(text) if available else []

        return {
            "affordance_type": "QuantResearchAuthoringAffordance",
            "available": available,
            "draft_targets": draft_targets,
            "allowed_actions": self._allowed_actions(draft_targets),
            "blocked_actions": ["execute_strategy", "publish_capability", "write_tool_registry"],
            "requires_confirmation": available,
            "can_execute_without_confirmation": False,
            "handoff_contracts": self._handoff_contracts(draft_targets),
            "reason_codes": self._reason_codes(
                has_authoring_lane=has_authoring_lane,
                has_quant_language=has_quant_language,
                draft_targets=draft_targets,
            ),
        }

    def _lane_names(self, planner_question_contract: Mapping[str, Any]) -> set[str]:
        lanes = planner_question_contract.get("lanes") if isinstance(planner_question_contract.get("lanes"), list) else []
        return {
            self._trim(item.get("lane"))
            for item in lanes
            if isinstance(item, Mapping) and self._trim(item.get("lane"))
        }

    def _draft_targets(self, text: str) -> List[Dict[str, Any]]:
        targets = [
            {
                "target_type": "strategy_draft",
                "contract": "QuantStrategyAuthoringContract",
                "execution": "not_planned",
                "requires_confirmation": True,
            }
        ]
        if self._contains_any(text, self._SQL_TEMPLATE_TERMS):
            targets.append(
                {
                    "target_type": "sql_template_draft",
                    "contract": "SqlTemplateSpec",
                    "execution": "not_planned",
                    "requires_confirmation": True,
                }
            )
        return targets

    def _allowed_actions(self, draft_targets: List[Dict[str, Any]]) -> List[str]:
        actions = []
        target_types = {self._trim(item.get("target_type")) for item in draft_targets if isinstance(item, Mapping)}
        if "strategy_draft" in target_types:
            actions.append("prepare_strategy_authoring_contract")
        if "sql_template_draft" in target_types:
            actions.append("prepare_sql_template_draft")
        return actions

    def _handoff_contracts(self, draft_targets: List[Dict[str, Any]]) -> List[str]:
        contracts: List[str] = []
        for item in draft_targets:
            contract = self._trim(item.get("contract")) if isinstance(item, Mapping) else ""
            if contract and contract not in contracts:
                contracts.append(contract)
        return contracts

    def _reason_codes(
        self,
        *,
        has_authoring_lane: bool,
        has_quant_language: bool,
        draft_targets: List[Dict[str, Any]],
    ) -> List[str]:
        reasons: List[str] = []
        if has_authoring_lane:
            reasons.append("planner_authoring_lane_available")
        if has_quant_language:
            reasons.append("quant_authoring_language_detected")
        if any(item.get("target_type") == "sql_template_draft" for item in draft_targets):
            reasons.append("sql_template_authoring_language_detected")
        return reasons
