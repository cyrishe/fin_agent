"""Install per-user macOS launchd jobs for model preparation and 14:40 scoring."""
from __future__ import annotations

import argparse
import getpass
import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/automl_1440.sh"
LABEL = "com.finagent.automl1440"


def job(name: str, hour: int, minute: int, user_id: str, log: Path) -> dict:
    args = [str(WRAPPER), name, "--user-id", user_id]
    if name == "run":
        args.append("--write-db")
    return {"Label": f"{LABEL}.{name}", "ProgramArguments": args,
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "WorkingDirectory": str(ROOT), "StandardOutPath": str(log / f"{name}.out.log"),
            "StandardErrorPath": str(log / f"{name}.err.log"),
            "RunAtLoad": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default=getpass.getuser())
    parser.add_argument("--install", action="store_true",
                        help="Bootstrap the two LaunchAgents after writing plists")
    args = parser.parse_args()
    agents = Path.home() / "Library/LaunchAgents"
    log = ROOT / "outputs/stock_automl/live_1440/logs"
    agents.mkdir(parents=True, exist_ok=True)
    log.mkdir(parents=True, exist_ok=True)
    WRAPPER.chmod(0o755)
    # Training uses the completed current-day 09:40 label, so it runs after
    # that bar and before the 14:40 quote capture.
    for name, hour, minute in (("prepare", 13, 50), ("run", 14, 40)):
        path = agents / f"{LABEL}.{name}.plist"
        with path.open("wb") as file:
            plistlib.dump(job(name, hour, minute, args.user_id, log), file)
        print(path)
        if args.install:
            target = f"gui/{os.getuid()}/{LABEL}.{name}"
            subprocess.run(["launchctl", "bootout", target], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
                           check=True)
            print(f"installed {target}")


if __name__ == "__main__":
    main()
