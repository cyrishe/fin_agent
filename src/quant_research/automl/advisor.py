"""SOFT planner/reviewer. Only aggregates go to the configured model; never prices or SQL."""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

PROMPTS = Path(__file__).with_name("prompts")


class ResearchAdvisor:
    def __init__(self, complete=None):
        self.complete = complete or self._complete

    @staticmethod
    def _complete(system, payload, *, enable_thinking=False):
        base = os.getenv("LLM_BASE_URL") or os.getenv("LLM_ENDPOINT")
        key = os.getenv("LLM_API_KEY") or os.getenv("LLM_KEY") or os.getenv("DASHSCOPE_API_KEY")
        model = os.getenv("LLM_DEFAULT_MODEL")
        if not all([base, key, model]):
            raise ValueError("configured LLM endpoint, key and model required")
        response = requests.post(base.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "system", "content": system},
                  {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                  "temperature": .2, "enable_thinking": enable_thinking,
                  "max_tokens": min(int(os.getenv("LLM_DEFAULT_MAX_TOKENS", "8192")), 8192)}, timeout=(8, 90))
        if not response.ok:
            # Server errors can contain URLs or credentials. Persist only the status code.
            raise RuntimeError(f"LLM HTTP {response.status_code}")
        choice = response.json()["choices"][0]
        content = choice["message"].get("content")
        if choice.get("finish_reason") == "length" or not isinstance(content, str) or not content.strip():
            raise ValueError("LLM returned empty or truncated content")
        return content

    def plan(self, spec, candidates, validation=(), budget=None):
        text = self.complete((PROMPTS / "plan.md").read_text(), {
            "objective": spec.objective, "constraints": spec.to_dict(),
            "round_trial_budget": budget or spec.max_trials, "candidates": candidates, "development_results": list(validation)})
        # The model selects existing candidate IDs. It cannot write code, SQL, budgets or source mappings.
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(cleaned)
        ids = result.get("candidate_ids", [])
        allowed = {c["id"] for c in candidates}
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in allowed for i in ids):
            raise ValueError("planner must select available candidate IDs")
        return list(dict.fromkeys(ids)), str(result.get("analysis", ""))

    def review(self, evidence):
        return self.complete((PROMPTS / "review.md").read_text(), evidence)
