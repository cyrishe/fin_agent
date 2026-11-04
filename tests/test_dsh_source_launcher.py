import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("explicit", [False, True])
def test_launcher_uses_project_python_or_explicit_override(tmp_path, explicit):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts/dsh_source_runtime.sh"
    launcher = scripts / source.name
    launcher.write_bytes(source.read_bytes())
    project_python = tmp_path / ".venv/bin/python"
    project_python.parent.mkdir(parents=True)
    project_python.write_text('#!/bin/sh\nprintf "project-python\\n"\nprintf "%s\\n" "$@"\n')
    project_python.chmod(0o755)
    override = tmp_path / "override-python"
    override.write_text('#!/bin/sh\nprintf "explicit-python\\n"\nprintf "%s\\n" "$@"\n')
    override.chmod(0o755)
    env = os.environ.copy()
    env.pop("FIN_AGENT_DSH_PYTHON", None)
    if explicit:
        env["FIN_AGENT_DSH_PYTHON"] = str(override)
    result = subprocess.run(["/bin/sh", str(launcher), "--help"], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [
        "explicit-python" if explicit else "project-python",
        str(scripts / "dsh_source_runtime.py"),
        "--help",
    ]
