from __future__ import annotations

import datetime as dt
import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping, Sequence

from src.backtest import BacktestError
from src.services.custom_tool_service import (
    CustomToolError,
    CustomToolRuntimeService,
    CustomToolStoreService,
)
from src.services.finance_tool_profile_service import (
    FinanceToolProfileError,
    FinanceToolProfileService,
)
from src.services.strategy_revision_contract_service import (
    StrategyRevisionContractError,
    StrategyRevisionContractService,
)
from src.services.strategy_run_service import StrategyReference


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CustomToolHistoricalReplayHost:
    """Owner-authorized, revision-pinned Custom Tool replay host.

    The host is intentionally not a product route.  It freezes one immutable
    strategy revision during preflight and injects trusted time/scope facts only
    into the finance runtime, never into the Tool's public business arguments.
    ``point_in_time_enforced`` means finance-data visibility is cutoff-safe; it
    does not claim that arbitrary Tool code is free of clock or random inputs.
    """

    SUPPORTED_KIND = "tool"
    SUPPORTED_VERSION = "v1"

    def __init__(
        self,
        *,
        owner_ids: Sequence[str],
        store: CustomToolStoreService | None = None,
        runtime: CustomToolRuntimeService | None = None,
    ) -> None:
        self.owner_ids = tuple(
            dict.fromkeys(_trim(item) for item in owner_ids if _trim(item))
        )
        if not self.owner_ids:
            raise BacktestError(
                "historical_owner_required",
                "server-resolved owner identity is required for historical replay",
            )
        self.store = store or CustomToolStoreService()
        self.runtime = runtime or CustomToolRuntimeService(store=self.store)
        self._strategy_ref: StrategyReference | None = None
        self._bundle: dict[str, Any] | None = None
        self._asset_fingerprint = ""
        self._contract_assessment: dict[str, Any] = {}
        self._runtime_isolation: dict[str, Any] = {}

    def preflight_historical_replay(
        self,
        *,
        strategy_ref: StrategyReference,
    ) -> Mapping[str, Any]:
        self._validate_reference(strategy_ref)
        if self._strategy_ref is not None:
            self._assert_same_reference(strategy_ref)
            return self._preflight_evidence()

        assert strategy_ref.revision is not None
        try:
            bundle = self.store.load_revision_for_runtime(
                strategy_ref.name,
                strategy_ref.revision,
                owner_ids=self.owner_ids,
            )
        except CustomToolError as exc:
            raise BacktestError(
                "historical_asset_unavailable",
                "the requested Custom Tool revision is unavailable to this user",
                details={
                    "tool_name": strategy_ref.name,
                    "revision": strategy_ref.revision,
                },
            ) from exc

        manifest = bundle.get("manifest")
        if not isinstance(manifest, Mapping):
            raise BacktestError(
                "invalid_historical_asset",
                "the Custom Tool revision manifest is unavailable",
            )
        if _trim(manifest.get("tool_name")) != strategy_ref.name:
            raise BacktestError(
                "historical_asset_identity_mismatch",
                "the loaded Custom Tool identity does not match the requested strategy",
            )
        if int(manifest.get("current_revision") or 0) != strategy_ref.revision:
            raise BacktestError(
                "historical_asset_revision_mismatch",
                "the loaded Custom Tool revision does not match the requested revision",
            )

        assessment = self._assess_contracts(bundle)
        if assessment.get("portfolio_backtest_contract_ready") is not True:
            raise BacktestError(
                "strategy_backtest_contract_required",
                "the Custom Tool revision does not declare a ranked-selection backtest contract",
            )
        code = _trim(bundle.get("code"))
        if not code:
            raise BacktestError(
                "invalid_historical_asset",
                "the Custom Tool revision has no executable code",
            )

        frozen = deepcopy(dict(bundle))
        try:
            runtime_isolation = self.runtime.preflight_historical_replay(
                bundle=frozen
            )
        except (AttributeError, CustomToolError) as exc:
            raise BacktestError(
                "historical_runtime_isolation_required",
                "historical Custom Tool replay requires a formal isolated runtime",
            ) from exc
        if (
            not isinstance(runtime_isolation, Mapping)
            or runtime_isolation.get("formal_sandbox") is not True
        ):
            raise BacktestError(
                "historical_runtime_isolation_required",
                "historical Custom Tool replay requires a formal isolated runtime",
            )
        self._strategy_ref = strategy_ref
        self._bundle = frozen
        self._contract_assessment = dict(assessment)
        self._runtime_isolation = dict(runtime_isolation)
        self._asset_fingerprint = self._bundle_fingerprint(frozen)
        return self._preflight_evidence()

    def invoke(
        self,
        *,
        strategy_ref: StrategyReference,
        arguments: Mapping[str, Any],
        runtime_context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if self._bundle is None or self._strategy_ref is None:
            raise BacktestError(
                "historical_host_not_preflighted",
                "historical Custom Tool host must complete preflight before invocation",
            )
        self._assert_same_reference(strategy_ref)
        strategy_run = (
            runtime_context.get("strategy_run")
            if isinstance(runtime_context.get("strategy_run"), Mapping)
            else {}
        )
        invocation = (
            runtime_context.get("strategy_invocation")
            if isinstance(runtime_context.get("strategy_invocation"), Mapping)
            else {}
        )
        effective_as_of = self._date(strategy_run.get("effective_as_of"))
        subjects = tuple(
            _trim(item).upper()
            for item in invocation.get("subjects") or []
            if _trim(item)
        )
        if not subjects:
            raise BacktestError(
                "historical_scope_required",
                "historical Custom Tool invocation requires a frozen security scope",
            )

        result = self.runtime.run_loaded_bundle(
            bundle=deepcopy(self._bundle),
            arguments=dict(arguments or {}),
            effective_as_of=effective_as_of,
            allowed_symbols=subjects,
            runtime_backend=_trim(self._runtime_isolation.get("backend")),
        )
        if not isinstance(result, Mapping):
            raise BacktestError(
                "invalid_historical_tool_result",
                "historical Custom Tool runtime returned an invalid envelope",
            )
        payload = dict(result)
        meta = payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {}
        payload["meta"] = {
            **dict(meta),
            "historical_replay": {
                "effective_as_of": effective_as_of.isoformat(),
                "asset_fingerprint": self._asset_fingerprint,
                "revision": strategy_ref.revision,
            },
        }
        return payload

    def _assess_contracts(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        try:
            finance_profile = FinanceToolProfileService().normalize(
                bundle.get("finance_tool_profile"),
                strategy_runtime_profile=bundle.get("strategy_runtime_profile"),
                selection_output_profile=bundle.get("selection_output_profile"),
            )
            FinanceToolProfileService.assert_implementation_allowed(finance_profile)
            return StrategyRevisionContractService().assess(
                runtime_profile=bundle.get("strategy_runtime_profile"),
                selection_output_profile=bundle.get("selection_output_profile"),
                input_schema=(
                    bundle.get("input_schema")
                    if isinstance(bundle.get("input_schema"), Mapping)
                    else {}
                ),
                output_schema=(
                    bundle.get("output_schema")
                    if isinstance(bundle.get("output_schema"), Mapping)
                    else {}
                ),
            )
        except (FinanceToolProfileError, StrategyRevisionContractError) as exc:
            raise BacktestError(
                "invalid_strategy_revision_contract",
                "the Custom Tool strategy companion contract is invalid",
                details={"reason": str(exc)},
            ) from exc

    def _preflight_evidence(self) -> dict[str, Any]:
        assert self._strategy_ref is not None
        return {
            "authorized": True,
            "revision_pinned": True,
            "point_in_time_enforced": True,
            "asset": f"{self._strategy_ref.name}@{self._strategy_ref.revision}",
            "asset_fingerprint": self._asset_fingerprint,
            "revision": self._strategy_ref.revision,
            "cutoff_policy": (
                "stock.quote.dynamic_cal raw trade dates <= effective_as_of; "
                "realtime and non-snapshotted identity fields disabled"
            ),
            "field_policy": "raw trade-date-bound quote columns only",
            "allowed_finance_apis": ["stock.quote.dynamic_cal"],
            "runtime_isolation": dict(self._runtime_isolation),
            "contract": dict(self._contract_assessment),
        }

    @classmethod
    def _validate_reference(cls, strategy_ref: StrategyReference) -> None:
        if not isinstance(strategy_ref, StrategyReference):
            raise BacktestError(
                "invalid_strategy_ref",
                "historical replay requires a resolved StrategyReference",
            )
        if strategy_ref.kind != cls.SUPPORTED_KIND:
            raise BacktestError(
                "unsupported_historical_asset_kind",
                "the first historical replay host supports Custom Tools only",
            )
        if strategy_ref.version != cls.SUPPORTED_VERSION:
            raise BacktestError(
                "unsupported_historical_asset_version",
                "historical Custom Tool replay currently supports version v1 only",
            )
        if strategy_ref.revision is None or strategy_ref.revision < 1:
            raise BacktestError(
                "historical_revision_required",
                "historical Custom Tool replay requires an explicit positive revision",
            )

    def _assert_same_reference(self, strategy_ref: StrategyReference) -> None:
        if strategy_ref != self._strategy_ref:
            raise BacktestError(
                "historical_asset_identity_mismatch",
                "historical host invocation does not match the preflighted asset",
            )

    @staticmethod
    def _date(value: Any) -> dt.date:
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(_trim(value))
        except ValueError as exc:
            raise BacktestError(
                "historical_cutoff_required",
                "historical invocation requires an ISO effective_as_of date",
            ) from exc

    @staticmethod
    def _bundle_fingerprint(bundle: Mapping[str, Any]) -> str:
        manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), Mapping) else {}
        storage = bundle.get("storage") if isinstance(bundle.get("storage"), Mapping) else {}
        storage_identity = {
            key: storage.get(key)
            for key in ("kind", "artifact_id", "revision_id", "revision")
            if storage.get(key) not in (None, "")
        }
        return _fingerprint(
            {
                "tool_name": _trim(manifest.get("tool_name")),
                "revision": int(manifest.get("current_revision") or 0),
                "visibility": _trim(manifest.get("visibility")),
                "storage": storage_identity,
                "code": _trim(bundle.get("code")),
                "input_schema": bundle.get("input_schema") or {},
                "output_schema": bundle.get("output_schema") or {},
                "finance_tool_profile": bundle.get("finance_tool_profile") or {},
                "strategy_runtime_profile": bundle.get("strategy_runtime_profile") or {},
                "selection_output_profile": bundle.get("selection_output_profile") or {},
            }
        )


__all__ = ["CustomToolHistoricalReplayHost"]
