#!/usr/bin/env bash
# Run interactively as the deployment owner; sudo is used only for Web restart.
set -euo pipefail
cd /home/che/cyris/fin_agent
release=/home/che/cyris/fin_agent_deploy/web-search-20260914-e7bb931
printf '检查代码与构建版本……\n'
if ! git merge-base --is-ancestor e7bb931 HEAD ||
   ! git --no-pager diff --quiet e7bb931 -- src frontend requirements-finance-api.txt ||
   ! git --no-pager diff --cached --quiet -- src frontend requirements-finance-api.txt; then
  printf '停止：代码与本次构建不一致，尚未重启或切换前端。\n' >&2
  exit 1
fi
test -s "$release/frontend-next/index.html"
test -s "$release/frontend-before/index.html"
printf '代码检查通过。验证服务重启权限……\n'
if ! sudo -v; then
  printf '停止：需要部署账号的 sudo 授权，尚未重启或切换前端。\n' >&2
  exit 1
fi
printf '预置前端资源并重启 Web 服务……\n'
cp -an "$release/frontend-next/assets/." frontend/dist/assets/
sudo systemctl restart fin-agent-web.service
systemctl is-active --quiet fin-agent-web.service
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:22056/skills/studio \
  --output "$release/route-after-restart.html"
cmp "$release/route-after-restart.html" frontend/dist/index.html
printf '服务响应正常，切换前端入口……\n'
cp "$release/frontend-next/index.html" frontend/dist/index.html.web-search-next
mv frontend/dist/index.html.web-search-next frontend/dist/index.html
curl --fail --silent --show-error --max-time 15 \
  https://ai-agent.kingdomai.com/fin_agent/skills/studio \
  --output "$release/public-after-activation.html"
cmp "$release/public-after-activation.html" frontend/dist/index.html
sha256sum frontend/dist/index.html
systemctl show fin-agent-web.service -p MainPID -p ActiveState -p ActiveEnterTimestamp
printf '发布激活完成，公网入口校验通过。\n'
