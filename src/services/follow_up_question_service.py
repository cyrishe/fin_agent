"""Optional SOFT follow-ups; failure never changes the completed answer."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.utils import ai_service

LOG = logging.getLogger(__name__)
PROMPT = Path(__file__).resolve().parents[1] / "scenarios/financial_qa/follow_up_questions.md"


class FollowUpQuestionService:
    def __init__(self, complete=None):
        self.complete = complete or self._complete

    @staticmethod
    def _complete(messages):
        return ai_service._create_llm_completion(
            messages, model=ai_service.DEFAULT_FLASH_MODEL, max_tokens=512,
            temperature=0.3, enable_think=False, response_format={"type": "json_object"},
            client_instance=ai_service.llm_client.with_options(timeout=8.0, max_retries=0),
        )

    def generate(self, *, question: str, answer: str) -> dict:
        usage = {}
        try:
            response = self.complete([
                {"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
                {"role": "user", "content": json.dumps({
                    "question": question[:4000],
                    # Preserve both the conclusions and limitations of long answers.
                    "answer": answer if len(answer) <= 16000 else answer[:12000] + "\n…\n" + answer[-4000:],
                }, ensure_ascii=False)},
            ])
            raw_usage = response.usage
            usage = raw_usage.model_dump() if hasattr(raw_usage, "model_dump") else dict(raw_usage or {})
            usage["call_count"] = 1
            payload = json.loads(response.choices[0].message.content or "{}")
            raw = payload.get("questions", []) if isinstance(payload, dict) else []
            questions = list(dict.fromkeys(item.strip() for item in raw if isinstance(item, str) and item.strip()))[:3] if isinstance(raw, list) else []
            return {"questions": questions, "llm_usage": usage}
        except Exception:
            LOG.warning("Follow-up generation unavailable", exc_info=True)
            return {"questions": [], "llm_usage": usage}
