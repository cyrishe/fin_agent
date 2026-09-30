#!/usr/bin/env bash
# The deployment owner runs this after reviewing the prepared release receipt.
# Example: bash deploy/server/ai-agent-kingdomai/activate-release.sh /path/to/release
set -euo pipefail
release=$(realpath "${1:?Provide the prepared release directory}")
cd /home/che/cyris/fin_agent
test "$(git rev-parse HEAD)" = "$(cat "$release/commit.txt")"
git diff --exit-code HEAD -- src config frontend requirements-finance-api.txt deploy/dsh
test -s "$release/frontend-next/index.html"
test -s "$release/frontend-before/index.html"
(cd "$release/frontend-next" && sha256sum --check "$release/frontend.sha256" >/dev/null)
test "$(git -C /home/che/cyris/fin_harness rev-parse HEAD)" = "$(.venv/bin/python -c 'import json; print(json.load(open("deploy/dsh/fin_harness.lock.json"))["commit"])')"
git -C /home/che/cyris/fin_harness diff --exit-code HEAD
cp -an "$release/frontend-next/assets/." frontend/dist/assets/
sudo systemctl restart fin-agent-web.service fin-agent-finance-api.service
systemctl is-active --quiet fin-agent-web.service fin-agent-finance-api.service
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:22054/health --output "$release/health-after-restart.json"
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:22056/skills/studio --output "$release/web-after-restart.html"
cp "$release/frontend-next/index.html" frontend/dist/index.html.release-next
mv frontend/dist/index.html.release-next frontend/dist/index.html
curl --fail --silent --show-error --max-time 15 \
  https://ai-agent.kingdomai.com/fin_agent/skills/studio --output "$release/public-after-activation.html"
cmp "$release/public-after-activation.html" frontend/dist/index.html
systemctl show fin-agent-web.service fin-agent-finance-api.service -p Id -p MainPID -p ActiveState -p ActiveEnterTimestamp
echo 'Release activated. Run the public API business cases to confirm the new behavior.'
