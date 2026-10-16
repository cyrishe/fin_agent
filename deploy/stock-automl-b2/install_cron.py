"""Install B2's own cron block, preserving all existing jobs and a backup."""
from datetime import datetime
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
assert (root / "assets/verification_monthly.json").exists()
assert (root / "logs/historical_verification_final.json").exists()
old = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
if old.returncode not in (0, 1):
    raise RuntimeError(old.stderr)
text = old.stdout
start, end = "# BEGIN FIN_AGENT_B2", "# END FIN_AGENT_B2"
if start in text:
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    text = before.rstrip() + "\n" + after.lstrip()
backup = root / "logs" / ("crontab_before_B2_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
backup.write_text(old.stdout)
block = f"""{start}
20 16,18,20 * * 1-5 {root}/b2.sh close --write-db >> {root}/logs/close.log 2>&1
10 16,18,20 * * 1-5 {root}/b2.sh settle --write-db >> {root}/logs/settle.log 2>&1
30-40 9 * * 1-5 {root}/b2.sh scan --watch --write-db >> {root}/logs/scan.log 2>&1
{end}
"""
target = root / "logs/crontab_with_B2.txt"
target.write_text(text.rstrip() + "\n\n" + block)
subprocess.run(["crontab", str(target)], check=True)
assert subprocess.check_output(["crontab", "-l"], text=True) == target.read_text()
print(f"B2 cron installed; prior crontab saved: {backup}")
