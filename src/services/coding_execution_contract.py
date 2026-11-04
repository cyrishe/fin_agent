from __future__ import annotations

from pathlib import Path
import re
import shlex
import sys
from typing import Iterable


_UNSAFE_SHELL_TOKENS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "${", "\n", "\r")
_ENV_NAMES = {"PYTHONPYCACHEPREFIX", "PYTHONPATH"}


def python_executable() -> str:
    return str(Path(sys.executable).resolve())


def compile_command(module_path: str) -> str:
    return (
        f"PYTHONPYCACHEPREFIX=scratch/pycache "
        f"{python_executable()} -m py_compile {module_path}"
    )


def focused_test_command(test_path: str) -> str:
    return f"PYTHONPATH=dev_runtime {python_executable()} {test_path}"


def coding_command_denial_reason(
    command: str,
    *,
    cwd: Path,
    allowed_module_paths: Iterable[Path],
) -> str:
    """Allow only the compile and focused-test commands documented to Coding agents."""
    if not command or any(token in command for token in _UNSAFE_SHELL_TOKENS):
        return "only isolated Python compile/test commands are allowed"
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "shell command could not be parsed"

    env: dict[str, str] = {}
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        name, value = tokens.pop(0).split("=", 1)
        if name not in _ENV_NAMES:
            return "shell environment is outside the coding execution contract"
        env[name] = value

    if not tokens:
        return "only isolated Python compile/test commands are allowed"
    executable = tokens.pop(0)
    allowed_python = {python_executable(), "python", "python3"}
    if executable not in allowed_python:
        return "only the configured Python interpreter is allowed"

    if "PYTHONPYCACHEPREFIX" in env and not _inside(
        _resolve(env["PYTHONPYCACHEPREFIX"], cwd),
        cwd / "scratch",
    ):
        return "Python cache must stay inside the run workspace scratch directory"
    if "PYTHONPATH" in env and env["PYTHONPATH"] != "dev_runtime":
        return "PYTHONPATH may only expose the isolated dev runtime"

    if tokens[:2] == ["-m", "py_compile"]:
        targets = tokens[2:]
        allowed = {path.resolve() for path in allowed_module_paths}
        if targets and all(_resolve(target, cwd) in allowed for target in targets):
            return ""
        return "compile target is outside the editable module allowlist"

    if tokens[:2] == ["-m", "pytest"]:
        return _focused_test_args_denial_reason(tokens[2:], cwd)

    while tokens and tokens[0] in {"-u", "-B"}:
        tokens.pop(0)
    if tokens and _inside(_resolve(tokens[0], cwd), cwd / "scratch"):
        return ""
    return "focused test scripts must be stored under scratch"


def _focused_test_args_denial_reason(args: list[str], cwd: Path) -> str:
    targets = [arg for arg in args if not arg.startswith("-")]
    if targets and all(_inside(_resolve(target.split("::", 1)[0], cwd), cwd / "scratch") for target in targets):
        return ""
    return "pytest targets must be stored under scratch"


def _resolve(value: str, cwd: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else cwd / path).resolve()


def _inside(path: Path, root: Path) -> bool:
    root = root.resolve()
    return path == root or path.is_relative_to(root)
