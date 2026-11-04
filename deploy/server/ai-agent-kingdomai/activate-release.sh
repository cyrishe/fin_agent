#!/usr/bin/env bash
# The deployment owner runs this after reviewing the prepared release receipt.
# Example: bash deploy/server/ai-agent-kingdomai/activate-release.sh /path/to/release [--check]
set -euo pipefail
release=${1:?Provide the prepared release directory}
check_only=${2:-}
if [[ $# -gt 2 || ( -n "$check_only" && "$check_only" != --check ) ]]; then
  echo 'Usage: activate-release.sh RELEASE_DIRECTORY [--check]' >&2
  exit 2
fi
caller=$(id -un)
if [[ $(id -u) != 0 && ( "$check_only" != --check || "$caller" != che ) ]]; then
  echo '需要 sudo 授权；服务由管理员重启，发布文件仍以 che 身份读写。' >&2
  exec sudo -- bash "$0" "$@"
fi
as_che() {
  if [[ "$caller" == che ]]; then "$@"; else runuser -u che -- "$@"; fi
}
# Keep the caller's logical /home/che/cyris path; permissions belong to the
# target directory regardless of whether it is reached through this symlink.
release=$(as_che bash -c 'cd -- "$1" && pwd -L' -- "$release")
cd /home/che/cyris/fin_agent
test "$(as_che git rev-parse HEAD)" = "$(as_che cat "$release/commit.txt")"
as_che git diff --exit-code HEAD -- src config frontend requirements-finance-api.txt deploy/dsh
as_che test -s "$release/frontend-next/index.html"
as_che test -s "$release/frontend-before/index.html"
as_che bash -c 'cd -- "$1/frontend-next" && sha256sum --check "$1/frontend.sha256" >/dev/null' -- "$release"
test "$(as_che git -C /home/che/cyris/fin_harness rev-parse HEAD)" = "$(as_che .venv/bin/python -c 'import json; print(json.load(open("deploy/dsh/fin_harness.lock.json"))["commit"])')"
as_che git -C /home/che/cyris/fin_harness diff --exit-code HEAD
if [[ "$check_only" == --check ]]; then
  echo "检查通过，未重启服务、未切换前端：$release"
  exit 0
fi
as_che cp -an "$release/frontend-next/assets/." frontend/dist/assets/
systemctl restart fin-agent-web.service fin-agent-finance-api.service
systemctl is-active --quiet fin-agent-web.service fin-agent-finance-api.service
as_che curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:22054/health --output "$release/health-after-restart.json"
as_che curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:22056/skills/studio --output "$release/web-after-restart.html"
as_che cp "$release/frontend-next/index.html" frontend/dist/index.html.release-next
as_che mv frontend/dist/index.html.release-next frontend/dist/index.html
as_che curl --fail --silent --show-error --max-time 15 \
  https://ai-agent.kingdomai.com/fin_agent/skills/studio --output "$release/public-after-activation.html"
as_che cmp "$release/public-after-activation.html" frontend/dist/index.html
systemctl show fin-agent-web.service fin-agent-finance-api.service -p Id -p MainPID -p ActiveState -p ActiveEnterTimestamp
echo 'Release activated. Run the public API business cases to confirm the new behavior.'
