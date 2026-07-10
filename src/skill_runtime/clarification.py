import uuid
from typing import Any, Dict, List


class ClarificationBuilder:
    SLOT_PROMPTS = {
        "code_or_name": "请补充股票名称或 6 位股票代码。",
        "task_focus": "请说明你更想做个股深度分析，还是热点/概念追踪。",
        "time_range": "请补充你关注的时间范围，例如今天、近两天或最近一周。",
    }

    def build(
        self,
        *,
        missing_slots: List[str],
        context_summary: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        prompts = [self.SLOT_PROMPTS.get(slot, f"请补充字段：{slot}") for slot in missing_slots]
        return {
            "state_type": "clarification_state",
            "status": "needs_user_input",
            "missing_slots": list(missing_slots or []),
            "prompt": " ".join(prompts).strip() or "请补充必要信息。",
            "resume_token": f"clarify_{uuid.uuid4().hex[:16]}",
            "slot_hints": {slot: self.SLOT_PROMPTS.get(slot, "") for slot in missing_slots},
            "context_summary": context_summary or {},
        }
