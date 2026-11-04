"""Exercise the deployment shell without sudo privileges or real services."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/server/ai-agent-kingdomai/activate-release.sh"


@pytest.fixture
def deployment(tmp_path):
    physical = tmp_path / "data/cyris"
    physical.mkdir(parents=True)
    logical = tmp_path / "home/che/cyris"
    logical.parent.mkdir(parents=True)
    logical.symlink_to(physical, target_is_directory=True)
    app = logical / "fin_agent"
    release = logical / "fin_agent_deploy/release"
    for directory in (app / "frontend/dist/assets", app / "deploy/dsh", app / ".venv/bin",
                      release / "frontend-next/assets", release / "frontend-before",
                      logical / "fin_harness"):
        directory.mkdir(parents=True)
    release.chmod(0o700)
    (release / "commit.txt").write_text("app-commit\n")
    (release / "frontend.sha256").write_text("fixture\n")
    (release / "frontend-next/index.html").write_text("new frontend")
    (release / "frontend-next/assets/new.js").write_text("new asset")
    (release / "frontend-before/index.html").write_text("old frontend")
    (app / "frontend/dist/index.html").write_text("old frontend")
    (app / "deploy/dsh/fin_harness.lock.json").write_text('{"commit":"harness-commit"}')
    (app / ".venv/bin/python").symlink_to(sys.executable)
    script = tmp_path / "activate-release.sh"
    script.write_text(SCRIPT.read_text().replace("/home/che/cyris", str(logical)))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command = bin_dir / "command.py"
    command.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps([name, args, os.environ.get("ACTING_AS", "root")]) + "\\n")
if name == "id":
    print(os.environ["CALLER"] if args == ["-un"] else os.environ["CALLER_UID"])
elif name == "sudo":
    # The launcher must return this failure before touching private files.
    sys.exit(77)
elif name == "runuser":
    assert args[:3] == ["-u", "che", "--"]
    os.environ["ACTING_AS"] = "che"
    os.execvp(args[3], args[3:])
elif name == "git":
    if "rev-parse" in args:
        print("harness-commit" if "-C" in args else "app-commit")
elif name == "systemctl":
    assert os.environ.get("ACTING_AS") != "che"
elif name == "curl":
    assert os.environ.get("ACTING_AS") == "che"
    Path(args[args.index("--output") + 1]).write_text("new frontend")
elif name == "sha256sum":
    pass
''')
    command.chmod(0o755)
    for name in ("id", "sudo", "runuser", "git", "systemctl", "curl", "sha256sum"):
        (bin_dir / name).symlink_to(command)
    log = tmp_path / "commands.jsonl"

    def run(caller, *options):
        env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
               "CALLER": caller, "CALLER_UID": "0" if caller == "root" else "1003",
               "COMMAND_LOG": str(log)}
        result = subprocess.run(["bash", str(script), str(release), *options],
                                env=env, text=True, capture_output=True, timeout=10)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    return run, release, app, script


@pytest.mark.parametrize("options", [(), ("--check",)])
def test_maintenance_account_elevates_before_private_directory_access(deployment, options):
    run, release, _, script = deployment
    result, calls = run("weihu", *options)
    assert result.returncode == 77
    assert calls[-1][:2] == ["sudo", ["--", "bash", str(script), str(release), *options]]
    assert {call[0] for call in calls} == {"id", "sudo"}
    assert "Permission denied" not in result.stderr


@pytest.mark.parametrize("caller", ["che", "root"])
def test_check_preserves_symlink_and_never_restarts_or_changes_frontend(deployment, caller):
    run, release, app, _ = deployment
    result, calls = run(caller, "--check")
    assert result.returncode == 0, result.stderr
    assert str(release) in result.stdout
    assert (app / "frontend/dist/index.html").read_text() == "old frontend"
    assert not (app / "frontend/dist/assets/new.js").exists()
    assert not any(call[0] in {"systemctl", "curl", "sudo"} for call in calls)
    if caller == "root":
        assert all(call[2] == "che" for call in calls if call[0] == "git")


def test_activation_uses_che_for_files_and_root_for_service_restart(deployment):
    run, release, app, _ = deployment
    result, calls = run("root")
    assert result.returncode == 0, result.stderr
    assert (app / "frontend/dist/index.html").read_text() == "new frontend"
    assert (app / "frontend/dist/assets/new.js").read_text() == "new asset"
    assert [call for call in calls if call[0] == "systemctl" and call[1][0] == "restart"] == [
        ["systemctl", ["restart", "fin-agent-web.service", "fin-agent-finance-api.service"], "root"]]
    assert all(call[2] == "che" for call in calls if call[0] in {"git", "curl"})
    for name in ("cp", "mv", "cmp"):
        assert any(call[0] == "runuser" and call[1][3] == name for call in calls)
    assert not any(call[0] == "sudo" for call in calls)
    assert release.stat().st_mode & 0o777 == 0o700


def test_wrong_commit_stops_before_restart(deployment):
    run, release, app, _ = deployment
    (release / "commit.txt").write_text("different-commit\n")
    result, calls = run("root")
    assert result.returncode != 0
    assert not any(call[0] in {"systemctl", "curl"} for call in calls)
    assert (app / "frontend/dist/index.html").read_text() == "old frontend"
