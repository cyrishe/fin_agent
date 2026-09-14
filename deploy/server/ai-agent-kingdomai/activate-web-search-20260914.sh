#!/usr/bin/env bash
# Run interactively as the deployment owner; sudo is used only for Web restart.
set -euo pipefail
cd /home/che/cyris/fin_agent
release=/home/che/cyris/fin_agent_deploy/web-search-20260914-e7bb931
git merge-base --is-ancestor e7bb931 HEAD
git diff --exit-code e7bb931 -- src frontend requirements-finance-api.txt
git diff --cached --exit-code -- src frontend requirements-finance-api.txt
test -s "$release/frontend-next/index.html"
test -s "$release/frontend-before/index.html"
cp -an "$release/frontend-next/assets/." frontend/dist/assets/
sudo systemctl restart fin-agent-web.service
systemctl is-active --quiet fin-agent-web.service
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:22056/skills/studio \
  --output "$release/route-after-restart.html"
cmp "$release/route-after-restart.html" frontend/dist/index.html
cp "$release/frontend-next/index.html" frontend/dist/index.html.web-search-next
mv frontend/dist/index.html.web-search-next frontend/dist/index.html
curl --fail --silent --show-error --max-time 15 \
  https://ai-agent.kingdomai.com/fin_agent/skills/studio \
  --output "$release/public-after-activation.html"
cmp "$release/public-after-activation.html" frontend/dist/index.html
sha256sum frontend/dist/index.html
systemctl show fin-agent-web.service -p MainPID -p ActiveState -p ActiveEnterTimestamp
