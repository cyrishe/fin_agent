#!/usr/bin/env bash
# Run interactively as the deployment owner; sudo is used only for Web restart.
set -euo pipefail
cd /home/che/cyris/fin_agent
release=/home/che/cyris/fin_agent_deploy/web-search-20260914-e7bb931
printf '检查代码与构建版本……\n'
printf '执行用户：%s；目录：%s\n' "$(id -un)" "$(pwd -P)"
git rev-parse --show-toplevel --short HEAD
if ! git merge-base --is-ancestor e7bb931 HEAD; then
  printf '停止：当前 Git 历史不包含构建提交 e7bb931，或 Git 无法读取仓库。尚未发布。\n' >&2
  exit 1
fi
# --exit-code checks content and --stat keeps diagnostics bounded to filenames.
# Do not collapse ancestry, working-tree and staged failures into one message.
if git --no-pager diff --no-ext-diff --exit-code --stat e7bb931 -- src frontend requirements-finance-api.txt; then
  :
else
  check_exit=$?
  printf '停止：源码与构建基线检查返回 %s（1 表示差异，其他表示检查错误）。尚未发布。\n' "$check_exit" >&2
  exit "$check_exit"
fi
if git --no-pager diff --no-ext-diff --cached --exit-code --stat -- src frontend requirements-finance-api.txt; then
  :
else
  check_exit=$?
  printf '停止：暂存区检查返回 %s（1 表示差异，其他表示检查错误）。尚未发布。\n' "$check_exit" >&2
  exit "$check_exit"
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
