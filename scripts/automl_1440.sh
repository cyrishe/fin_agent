#!/bin/zsh
set -euo pipefail
cd /Volumes/ext/fin_agent
exec /Volumes/ext/fin_agent/.venv/bin/python -m scripts.run_automl_1440_strategy "$@"
