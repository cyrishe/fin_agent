import datetime as dt
from typing import Any, Dict


class TimeResolver:
    """
    Minimal natural-language time normalizer for entrance routing.
    """

    def resolve(self, *, user_text: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        context = context or {}
        text = str(user_text or "").strip()
        today = dt.date.today()
        normalized = {
            "time_focus": "",
            "start_date": "",
            "end_date": "",
            "resolved": False,
        }
        if "今天" in text:
            normalized.update(
                {
                    "time_focus": "today",
                    "start_date": today.isoformat(),
                    "end_date": today.isoformat(),
                    "resolved": True,
                }
            )
            return normalized
        if "最近一周" in text or "近一周" in text:
            normalized.update(
                {
                    "time_focus": "last_7_days",
                    "start_date": (today - dt.timedelta(days=6)).isoformat(),
                    "end_date": today.isoformat(),
                    "resolved": True,
                }
            )
            return normalized
        if "这两天" in text or "近两天" in text:
            normalized.update(
                {
                    "time_focus": "last_2_days",
                    "start_date": (today - dt.timedelta(days=1)).isoformat(),
                    "end_date": today.isoformat(),
                    "resolved": True,
                }
            )
            return normalized
        explicit_as_of_date = str(context.get("as_of_date") or "").strip()
        if explicit_as_of_date:
            normalized.update(
                {
                    "time_focus": "as_of_date",
                    "start_date": explicit_as_of_date,
                    "end_date": explicit_as_of_date,
                    "resolved": True,
                }
            )
        return normalized
