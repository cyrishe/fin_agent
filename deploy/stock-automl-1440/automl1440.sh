#!/usr/bin/env bash
set -euo pipefail

release_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mode=${1:-}
if [[ "$mode" != prepare && "$mode" != run ]]; then
  echo "usage: $0 prepare|run [strategy arguments]" >&2
  exit 2
fi
shift

env_file=${AUTOML1440_ENV_FILE:-/home/che/cyris/fin_agent/.env}
user_id=${AUTOML1440_USER_ID:-imac}
python="$release_dir/.venv/bin/python"
[[ -x "$python" && -r "$env_file" ]] || { echo "Python environment or .env unavailable" >&2; exit 1; }
cd "$release_dir"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
"$python" - <<'PY'
import sklearn, numpy, scipy, pandas
actual = (sklearn.__version__, numpy.__version__, scipy.__version__, pandas.__version__)
expected = ('1.7.1', '2.5.2', '1.18.1', '2.3.3')
assert actual == expected, (actual, expected)
PY

mkdir -p logs outputs/stock_automl/live_1440/cache
exec 9>"$release_dir/automl1440.lock"
flock -n 9 || { echo "Another 14:40 job is active" >&2; exit 1; }
if [[ "$mode" == prepare ]]; then
  "$python" -m scripts.check_backfill_automl_previous_day --env-file "$env_file" --execute
fi
exec "$python" -m scripts.run_automl_1440_strategy "$mode" \
  --env-file "$env_file" --user-id "$user_id" "$@"
