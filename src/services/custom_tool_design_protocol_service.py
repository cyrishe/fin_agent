from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

class CustomToolDesignProtocolService:
    """System-owned user feedback evidence."""

    def append_feedback(
        self,
        ledger: Any,
        *,
        text: str,
        design_round: int,
        turn_id: Optional[int] = None,
        kind: str = "feedback",
    ) -> List[Dict[str, Any]]:
        entries = [dict(item) for item in (ledger or []) if isinstance(item, Mapping)]
        normalized = str(text or "").strip()
        digest = hashlib.sha256(f"{design_round}:{turn_id or 0}:{normalized}".encode("utf-8")).hexdigest()[:12]
        entries.append({
            "feedback_id": f"feedback_{digest}",
            "round": int(design_round),
            "turn_id": int(turn_id or 0),
            "kind": str(kind or "feedback"),
            "text": normalized,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        return entries
