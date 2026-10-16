#!/usr/bin/env bash
# Run as the deployment owner; sudo prompts only for the managed Web restart.
set -euo pipefail
cd /home/che/cyris/fin_agent
release=/home/che/cyris/fin_agent_deploy/skill-reader-20260911-b3c4536
git merge-base --is-ancestor b3c453646ef85849f09b1efee36030ffe5a0fd2a HEAD
# Refuse to activate this build over later or uncommitted application changes.
git diff --exit-code b3c453646ef85849f09b1efee36030ffe5a0fd2a -- frontend src/web/flask_app.py
test -s "$release/frontend-next/index.html"
test -s "$release/frontend-before/index.html"
cp -an "$release/frontend-next/assets/." frontend/dist/assets/
sudo systemctl restart fin-agent-web.service
systemctl is-active --quiet fin-agent-web.service
# The new route must serve the current index, not the old authoring template.
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:22056/skills/studio \
  --output "$release/route-after-restart.html"
cmp "$release/route-after-restart.html" frontend/dist/index.html
cp "$release/frontend-next/index.html" frontend/dist/index.html.skill-reader-next
mv frontend/dist/index.html.skill-reader-next frontend/dist/index.html
curl --fail --silent --show-error --max-time 15 \
  https://ai-agent.kingdomai.com/fin_agent/skills/studio \
  --output "$release/public-after-activation.html"
cmp "$release/public-after-activation.html" frontend/dist/index.html
sha256sum frontend/dist/index.html
systemctl show fin-agent-web.service -p MainPID -p ActiveState -p ActiveEnterTimestamp
