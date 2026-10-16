#!/usr/bin/env bash
set -euo pipefail
release_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mode=${1:-}
case "$mode" in register|close|scan|settle|report) ;; *) echo "usage: $0 register|close|scan|settle|report [args]" >&2; exit 2;; esac
shift
cd "$release_dir"
export TZ=Asia/Shanghai OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
env_file=${AUTOMLB2_ENV_FILE:-/home/che/cyris/fin_agent/.env}
python="$release_dir/.venv/bin/python"
[[ -r "$env_file" && -x "$python" ]] || { echo "B2 runtime or environment file unavailable" >&2; exit 1; }
"$python" - <<'PY'
import sklearn,numpy,scipy,pandas
actual=(sklearn.__version__,numpy.__version__,scipy.__version__,pandas.__version__)
assert actual==('1.7.1','2.5.2','1.18.1','2.3.3'), actual
PY
mkdir -p logs outputs/b2_live
exec 9>"$release_dir/b2.lock"
flock -n 9 || { echo "B2 is already running; skip duplicate invocation"; exit 0; }
exec "$python" -m scripts.run_automl_b2 "$mode" --env-file "$env_file" \
  --assets "$release_dir/assets" --output "$release_dir/outputs/b2_live" \
  --user-id "${AUTOMLB2_USER_ID:-imac}" --release B2_20261015 "$@"
