#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/tmp/local_services"
BACKEND_PORT="${FIN_AGENT_PORT:-22053}"
FRONTEND_PORT="${FIN_AGENT_FRONTEND_PORT:-22054}"
BACKEND_HOST="${FIN_AGENT_HOST:-127.0.0.1}"
LAUNCH_AGENT_DIR="${HOME}/Library/LaunchAgents"
LAUNCH_LOG_DIR="${HOME}/Library/Logs/fin_agent"
BACKEND_LABEL="com.finagent.local.backend"
FRONTEND_LABEL="com.finagent.local.frontend"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python)}"
NPM_BIN="${NPM_BIN:-$(command -v npm || true)}"
PATH_VALUE="${PATH}"

mkdir -p "${RUN_DIR}" "${LAUNCH_AGENT_DIR}" "${LAUNCH_LOG_DIR}"

stop_port() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    return 0
  fi

  echo "Stopping process(es) on ${port}: ${pids//$'\n'/ }"
  while read -r pid; do
    [[ -z "${pid}" ]] || kill "${pid}" 2>/dev/null || true
  done <<< "${pids}"

  for _ in {1..20}; do
    if ! lsof -tiTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done

  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Force-stopping remaining process(es) on ${port}: ${pids//$'\n'/ }"
    while read -r pid; do
      [[ -z "${pid}" ]] || kill -KILL "${pid}" 2>/dev/null || true
    done <<< "${pids}"
  fi

  for _ in {1..20}; do
    if ! lsof -tiTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done

  echo "Unable to release port ${port}." >&2
  return 1
}

start_backend() {
  echo "Starting backend on ${BACKEND_HOST}:${BACKEND_PORT}"
  cd "${ROOT_DIR}"
  nohup env \
    PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
    FIN_AGENT_PORT="${BACKEND_PORT}" \
    FIN_AGENT_FLASK_RELOADER="0" \
    "${PYTHON_BIN}" -m src.web.flask_app \
    </dev/null >"${RUN_DIR}/backend.log" 2>&1 &
  echo $! >"${RUN_DIR}/backend.pid"
}

start_frontend() {
  echo "Starting frontend on 127.0.0.1:${FRONTEND_PORT}"
  cd "${ROOT_DIR}/frontend"
  nohup "${NPM_BIN}" run dev -- --host 127.0.0.1 --port "${FRONTEND_PORT}" \
    </dev/null >"${RUN_DIR}/frontend.log" 2>&1 &
  echo $! >"${RUN_DIR}/frontend.pid"
}

launch_domain() {
  echo "gui/$(id -u)"
}

bootout_label() {
  local label="$1"
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi
  /bin/launchctl bootout "$(launch_domain)/${label}" >/dev/null 2>&1 || true
}

stop_services() {
  # Always unload both known LaunchAgents first. This is intentionally
  # independent of the next startup mode, so switching between launchctl and
  # nohup cannot leave an old supervisor alive to respawn a stopped process.
  bootout_label "${BACKEND_LABEL}"
  bootout_label "${FRONTEND_LABEL}"
  stop_port "${BACKEND_PORT}"
  stop_port "${FRONTEND_PORT}"
  rm -f "${RUN_DIR}/backend.pid" "${RUN_DIR}/frontend.pid"
}

write_backend_plist() {
  local plist="${LAUNCH_AGENT_DIR}/${BACKEND_LABEL}.plist"
  cat >"${plist}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${BACKEND_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd '${ROOT_DIR}' &amp;&amp; exec '${PYTHON_BIN}' -m src.web.flask_app</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PATH_VALUE}</string>
    <key>PYTHONPATH</key>
    <string>${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}</string>
    <key>FIN_AGENT_PORT</key>
    <string>${BACKEND_PORT}</string>
    <key>FIN_AGENT_FLASK_RELOADER</key>
    <string>0</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LAUNCH_LOG_DIR}/backend.log</string>
  <key>StandardErrorPath</key>
  <string>${LAUNCH_LOG_DIR}/backend.log</string>
</dict>
</plist>
EOF
  echo "${plist}"
}

write_frontend_plist() {
  local plist="${LAUNCH_AGENT_DIR}/${FRONTEND_LABEL}.plist"
  cat >"${plist}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${FRONTEND_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd '${ROOT_DIR}/frontend' &amp;&amp; exec '${NPM_BIN}' run dev -- --host 127.0.0.1 --port '${FRONTEND_PORT}'</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PATH_VALUE}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LAUNCH_LOG_DIR}/frontend.log</string>
  <key>StandardErrorPath</key>
  <string>${LAUNCH_LOG_DIR}/frontend.log</string>
</dict>
</plist>
EOF
  echo "${plist}"
}

start_with_launchctl() {
  if [[ "$(uname -s)" != "Darwin" ]] || [[ ! -x /bin/launchctl ]]; then
    return 1
  fi
  if [[ -z "${PYTHON_BIN}" || -z "${NPM_BIN}" ]]; then
    return 1
  fi

  echo "Starting services with launchctl"
  local backend_plist frontend_plist
  backend_plist="$(write_backend_plist)"
  frontend_plist="$(write_frontend_plist)"
  /bin/launchctl bootstrap "$(launch_domain)" "${backend_plist}"
  /bin/launchctl bootstrap "$(launch_domain)" "${frontend_plist}"
}

wait_for_url() {
  local url="$1"
  local label="$2"
  for _ in {1..40}; do
    if curl --silent --show-error --fail --max-time 2 "${url}" >/dev/null 2>&1; then
      echo "${label} is ready: ${url}"
      return 0
    fi
    sleep 0.5
  done
  echo "${label} did not become ready: ${url}" >&2
  return 1
}

USE_LAUNCHCTL="${FIN_AGENT_USE_LAUNCHCTL:-0}"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Python executable not found; set PYTHON_BIN explicitly." >&2
  exit 1
fi
if [[ -z "${NPM_BIN}" ]]; then
  echo "npm executable not found; set NPM_BIN explicitly." >&2
  exit 1
fi

stop_services
if [[ "${USE_LAUNCHCTL}" == "1" ]] && start_with_launchctl; then
  :
else
  start_backend
  start_frontend
fi

wait_for_url "http://${BACKEND_HOST}:${BACKEND_PORT}/" "backend"
wait_for_url "http://${BACKEND_HOST}:${BACKEND_PORT}/api/skill-hub" "backend Skill Hub"
wait_for_url "http://127.0.0.1:${FRONTEND_PORT}/" "frontend"

echo "Local services restarted."
echo "Backend:  http://${BACKEND_HOST}:${BACKEND_PORT}/"
echo "Frontend: http://127.0.0.1:${FRONTEND_PORT}/"
echo "Logs:     ${RUN_DIR}/backend.log and ${RUN_DIR}/frontend.log"
if [[ "${USE_LAUNCHCTL}" == "1" ]]; then
  echo "Launchctl logs: ${LAUNCH_LOG_DIR}/backend.log and ${LAUNCH_LOG_DIR}/frontend.log"
fi
