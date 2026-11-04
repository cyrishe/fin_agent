#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin=${FIN_AGENT_DSH_PYTHON:-}
if [ -z "$python_bin" ]; then
  if [ -x "$script_dir/../.venv/bin/python" ]; then
    python_bin="$script_dir/../.venv/bin/python"
  else
    python_bin=python3
  fi
fi

exec "$python_bin" "$script_dir/dsh_source_runtime.py" "$@"
