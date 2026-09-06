#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin=${FIN_AGENT_DSH_PYTHON:-python3}

exec "$python_bin" "$script_dir/dsh_source_runtime.py" "$@"
