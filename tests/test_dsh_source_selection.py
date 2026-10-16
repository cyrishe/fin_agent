from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.scenarios.financial_qa.dsh_service import (
    FinanceDeepSeekHarnessSessionService,
    _load_sdk_class,
)


def test_configured_checkout_supplies_sdk_and_cli_even_with_installed_dsh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    checkout = tmp_path / "fin_harness"
    package = checkout / "python/sdk/src/deepseek_harness"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("class DeepSeekHarness: pass\n")
    monkeypatch.setenv("FINANCE_DSH_SOURCE_ROOT", str(checkout))
    monkeypatch.delenv("FINANCE_DSH_SDK_SOURCE", raising=False)
    monkeypatch.delenv("FINANCE_DSH_BIN", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr("src.scenarios.financial_qa.dsh_service.shutil.which", lambda _: "/installed/dsh")

    previous = sys.modules.pop("deepseek_harness", None)
    try:
        sdk_class = _load_sdk_class()
        assert Path(sys.modules["deepseek_harness"].__file__).resolve() == package / "__init__.py"
        assert sdk_class.__name__ == "DeepSeekHarness"
        service = FinanceDeepSeekHarnessSessionService(enabled=False, root_dir=tmp_path / "runtime")
        assert service._dsh_bin() == service.repo_root / "scripts/dsh_source_runtime.sh"
    finally:
        sys.modules.pop("deepseek_harness", None)
        if previous is not None:
            sys.modules["deepseek_harness"] = previous


def test_checkout_rejects_mismatched_sdk_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FINANCE_DSH_SOURCE_ROOT", str(tmp_path / "fin_harness"))
    monkeypatch.setenv("FINANCE_DSH_SDK_SOURCE", str(tmp_path / "another/python/sdk/src"))
    with pytest.raises(RuntimeError, match="必须属于 FINANCE_DSH_SOURCE_ROOT"):
        _load_sdk_class()


def test_checkout_rejects_cached_sdk_from_another_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    checkout = tmp_path / "fin_harness"
    package = checkout / "python/sdk/src/deepseek_harness"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("class DeepSeekHarness: pass\n")
    monkeypatch.setenv("FINANCE_DSH_SOURCE_ROOT", str(checkout))
    monkeypatch.delenv("FINANCE_DSH_SDK_SOURCE", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "deepseek_harness", SimpleNamespace(
        __file__="/installed/deepseek_harness/__init__.py", DeepSeekHarness=object,
    ))
    with pytest.raises(RuntimeError, match="已从其他位置导入"):
        _load_sdk_class()


def test_unconfigured_runtime_accepts_installed_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINANCE_DSH_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("FINANCE_DSH_SDK_SOURCE", raising=False)
    installed_class = type("InstalledHarness", (), {})
    monkeypatch.setitem(sys.modules, "deepseek_harness", SimpleNamespace(
        __file__="/installed/deepseek_harness/__init__.py", DeepSeekHarness=installed_class,
    ))
    assert _load_sdk_class() is installed_class
