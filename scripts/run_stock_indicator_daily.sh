#!/usr/bin/env bash
# Run from cron using the deployment's own Python and configuration.
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
export TZ=Asia/Shanghai
mkdir -p data/stock_indicator_artifacts/runs
task_date="$(date +%F)"
exec .venv/bin/python scripts/stock_indicator_daily.py refresh --date "$task_date" \
  >> "data/stock_indicator_artifacts/runs/daily-${task_date}.log" 2>&1
