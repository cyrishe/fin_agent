from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from src.services.quant_research_publication_service import QuantResearchPublicationService


class QuantResearchCapabilityAdapterService:
    def __init__(
        self,
        *,
        publication_service: QuantResearchPublicationService | None = None,
        publication_root: str | Path | None = None,
    ) -> None:
        self.publication_service = publication_service or QuantResearchPublicationService(root=publication_root)

    def list_candidates(self, *, capability_type: str = "") -> Dict[str, Any]:
        normalized_type = str(capability_type or "").strip()
        active = self.publication_service.load_active_publications()
        candidates = [
            self._to_candidate(publication)
            for publication in active.values()
            if not normalized_type or publication.get("capability_type") == normalized_type
        ]
        candidates.sort(key=lambda item: (item["capability_type"], item["capability_id"], item["version"]))
        return {
            "source": "quant_research_publication_registry",
            "candidate_type": "quant_research_capability",
            "capability_type_filter": normalized_type,
            "count": len(candidates),
            "candidates": candidates,
        }

    def _to_candidate(self, publication: Dict[str, Any]) -> Dict[str, Any]:
        capability_id = str(publication.get("capability_id") or "").strip()
        display_name = str(publication.get("display_name") or "").strip() or capability_id
        spec_refs = [
            {
                "kind": str(item.get("kind") or ""),
                "id": str(item.get("id") or ""),
                "version": str(item.get("version") or ""),
            }
            for item in publication.get("spec_refs", [])
            if isinstance(item, dict)
        ]
        run_refs = [
            {
                "run_type": str(item.get("run_type") or ""),
                "run_ref": str(item.get("run_ref") or ""),
            }
            for item in publication.get("run_refs", [])
            if isinstance(item, dict)
        ]
        return {
            "capability_id": capability_id,
            "version": str(publication.get("version") or ""),
            "display_name": display_name,
            "capability_type": str(publication.get("capability_type") or ""),
            "purpose": self._purpose(publication, display_name),
            "best_for": self._best_for(publication),
            "entrypoint": dict(publication.get("entrypoint") or {}),
            "params_schema": dict(publication.get("params_schema") or {}),
            "spec_refs": spec_refs,
            "evidence": {
                "run_refs": run_refs,
                "publication_hash": str(publication.get("publication_hash") or ""),
            },
            "availability": {
                "lifecycle": "active",
                "retrieval_mode": "retrievable",
                "visibility": "visible",
            },
            "execution_policy": {
                "mode": "published_entrypoint_only",
                "read_only_candidate": True,
                "direct_execution": False,
            },
        }

    def _purpose(self, publication: Dict[str, Any], display_name: str) -> str:
        capability_type = str(publication.get("capability_type") or "")
        if capability_type == "strategy_pipeline":
            return f"Use published quant strategy pipeline: {display_name}"
        if capability_type == "backtest_report":
            return f"Use published quant backtest report capability: {display_name}"
        return f"Use published quant research capability: {display_name}"

    def _best_for(self, publication: Dict[str, Any]) -> List[str]:
        capability_type = str(publication.get("capability_type") or "")
        refs = publication.get("spec_refs") if isinstance(publication.get("spec_refs"), list) else []
        spec_labels = [
            f"{item.get('kind')}:{item.get('id')}"
            for item in refs
            if isinstance(item, dict) and item.get("kind") and item.get("id")
        ]
        if capability_type == "strategy_pipeline":
            return ["published_quant_strategy", "factor_based_screening", *spec_labels]
        if capability_type == "backtest_report":
            return ["published_quant_backtest", "strategy_evaluation", *spec_labels]
        return ["published_quant_research", *spec_labels]
