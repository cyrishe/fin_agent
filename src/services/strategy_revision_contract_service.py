from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.services.strategy_backtest_service import SelectionOutputProfile
from src.services.strategy_run_service import (
    STRATEGY_RUNTIME_PROFILE_PROTOCOL,
    StrategyRunError,
    StrategyRuntimeProfile,
)


class StrategyRevisionContractError(ValueError):
    """A strategy revision companion cannot be executed safely as declared."""


def _trim(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class StrategyRevisionContracts:
    """Canonical system-owned companions stored with one immutable revision."""

    runtime_profile: Mapping[str, Any] | None = None
    selection_output_profile: Mapping[str, Any] | None = None

    def to_bundle_fields(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.runtime_profile is not None:
            result["strategy_runtime_profile"] = dict(self.runtime_profile)
        if self.selection_output_profile is not None:
            result["selection_output_profile"] = dict(
                self.selection_output_profile
            )
        return result


class StrategyRevisionContractService:
    """Validate model-contributed hints and freeze the executable companion.

    The companion is deliberately separate from the public Tool input schema.
    Presence identifies a strategy revision; ordinary Tools omit both fields and
    retain their existing behavior.
    """

    def normalize(
        self,
        *,
        runtime_profile: Any,
        selection_output_profile: Any,
        input_schema: Mapping[str, Any] | None,
        output_schema: Mapping[str, Any] | None = None,
    ) -> StrategyRevisionContracts:
        if runtime_profile not in (None, "") and not isinstance(
            runtime_profile, Mapping
        ):
            raise StrategyRevisionContractError(
                "strategy_runtime_profile must be an object"
            )
        if selection_output_profile not in (None, "") and not isinstance(
            selection_output_profile, Mapping
        ):
            raise StrategyRevisionContractError(
                "selection_output_profile must be an object"
            )
        has_runtime = isinstance(runtime_profile, Mapping) and bool(runtime_profile)
        has_selection = isinstance(selection_output_profile, Mapping) and bool(
            selection_output_profile
        )
        if not has_runtime and not has_selection:
            return StrategyRevisionContracts()
        if not has_runtime:
            raise StrategyRevisionContractError(
                "selection_output_profile requires strategy_runtime_profile"
            )

        raw_runtime = dict(runtime_profile)
        protocol = _trim(raw_runtime.get("protocol"))
        if protocol and protocol != STRATEGY_RUNTIME_PROFILE_PROTOCOL:
            raise StrategyRevisionContractError(
                "unsupported strategy runtime profile protocol: "
                f"{protocol}"
            )
        try:
            profile = StrategyRuntimeProfile.from_mapping(raw_runtime)
        except StrategyRunError as exc:
            raise StrategyRevisionContractError(exc.message) from exc
        canonical_runtime = profile.to_dict()
        binding_type = self._validate_binding(
            profile=profile,
            input_schema=input_schema or {},
        )

        canonical_selection: Mapping[str, Any] | None = None
        if has_selection:
            if binding_type == "string":
                raise StrategyRevisionContractError(
                    "selection backtest profile requires one native universe "
                    "invocation, not scalar per-security dispatch"
                )
            raw_selection = dict(selection_output_profile)
            try:
                selection = SelectionOutputProfile(
                    candidate_path=_trim(raw_selection.get("candidate_path")),
                    symbol_field=_trim(raw_selection.get("symbol_field")),
                    output_date_path=_trim(
                        raw_selection.get("output_date_path")
                    ),
                )
            except ValueError as exc:
                message = getattr(exc, "message", "") or str(exc)
                raise StrategyRevisionContractError(message) from exc
            self._validate_selection_roots(
                selection=selection,
                output_schema=output_schema or {},
            )
            # The Custom Tool host owns the stable `{ok, data, ...}` result
            # envelope.  Models only need to identify public business fields;
            # accept either notation and persist one executable canonical form.
            selection = SelectionOutputProfile(
                candidate_path=self._host_result_path(selection.candidate_path),
                symbol_field=selection.symbol_field,
                output_date_path=self._host_result_path(
                    selection.output_date_path
                ),
            )
            canonical_selection = selection.to_dict()

        return StrategyRevisionContracts(
            runtime_profile=canonical_runtime,
            selection_output_profile=canonical_selection,
        )

    def assess(
        self,
        *,
        runtime_profile: Any,
        selection_output_profile: Any,
        input_schema: Mapping[str, Any] | None,
        output_schema: Mapping[str, Any] | None = None,
        execution_shape: str = "",
    ) -> dict[str, Any]:
        """Return derived UX facts; no additional lifecycle state is stored."""

        contracts = self.normalize(
            runtime_profile=runtime_profile,
            selection_output_profile=selection_output_profile,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        if contracts.runtime_profile is None:
            return {}
        profile = StrategyRuntimeProfile.from_mapping(contracts.runtime_profile)
        binding_type = self._validate_binding(
            profile=profile,
            input_schema=input_schema or {},
        )
        independent_entities = _trim(execution_shape).lower() == "entity_local"
        native_universe = binding_type in {"", "array"} and not independent_entities
        backtest_contract_ready = bool(
            native_universe and contracts.selection_output_profile
        )
        if backtest_contract_ready:
            summary = (
                "已具备一次性点时选股结果契约；历史回放仍需运行主机完成权限、"
                "固定修订号和 point-in-time 预检。"
            )
        elif binding_type == "string" or independent_entities:
            summary = (
                "逐股独立策略可由 Wrapper 有界并行运行；当前输出不能直接作为"
                "共享组合的每日排名。"
            )
        else:
            summary = (
                "策略可由 Wrapper 以点时 universe 单次运行，但尚未声明明确的"
                "有序选股结果契约。"
            )
        return {
            "strategy_wrapper_ready": True,
            "portfolio_backtest_contract_ready": backtest_contract_ready,
            "execution_shape": (
                "independent_entities"
                if binding_type == "string" or independent_entities
                else "native_universe"
            ),
            "summary": summary,
        }

    @staticmethod
    def _validate_binding(
        *,
        profile: StrategyRuntimeProfile,
        input_schema: Mapping[str, Any],
    ) -> str:
        field_name = profile.entity_argument
        if not field_name:
            return ""
        properties = input_schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        field_schema = properties.get(field_name)
        if not isinstance(field_schema, Mapping):
            raise StrategyRevisionContractError(
                "strategy runtime binding is absent from the public input schema: "
                f"{field_name}"
            )
        field_type = field_schema.get("type")
        allowed_types = (
            set(field_type) if isinstance(field_type, list) else {field_type}
        )
        if "array" in allowed_types:
            item_schema = field_schema.get("items")
            item_type = (
                item_schema.get("type")
                if isinstance(item_schema, Mapping)
                else None
            )
            allowed_item_types = (
                set(item_type) if isinstance(item_type, list) else {item_type}
            )
            if "string" not in allowed_item_types:
                raise StrategyRevisionContractError(
                    "strategy array binding must declare string items: "
                    f"{field_name}"
                )
            return "array"
        if "string" in allowed_types:
            return "string"
        raise StrategyRevisionContractError(
            "strategy runtime binding must be a string or array<string>: "
            f"{field_name}"
        )

    @staticmethod
    def _validate_selection_roots(
        *,
        selection: SelectionOutputProfile,
        output_schema: Mapping[str, Any],
    ) -> None:
        properties = output_schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        if not properties:
            return
        for field_name, path in (
            ("candidate_path", selection.candidate_path),
            ("output_date_path", selection.output_date_path),
        ):
            if not path:
                continue
            segments = path.split(".")
            # Custom Tool runtime results use `{ok, data, ...}`.  The leading
            # `data` addresses that stable host envelope and is not itself a
            # field in the Tool's public business output schema.
            if segments and segments[0] == "data":
                segments = segments[1:]
            root = segments[0] if segments else ""
            if root == "*" or root not in properties:
                raise StrategyRevisionContractError(
                    f"{field_name} root is absent from the public output schema: {root}"
                )

    @staticmethod
    def _host_result_path(path: str) -> str:
        normalized = _trim(path)
        if not normalized or normalized == "data" or normalized.startswith("data."):
            return normalized
        return f"data.{normalized}"


__all__ = [
    "StrategyRevisionContractError",
    "StrategyRevisionContracts",
    "StrategyRevisionContractService",
]
