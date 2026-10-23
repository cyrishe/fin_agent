#!/usr/bin/env bash
set -euo pipefail
cd /data/cyris/work/b2_2016_2020_r90_20261023
PY=/data/cyris/fin_agent_deploy/b2-v2-20261021/.venv/bin/python
case "${1:-results}" in
  data)
    "$PY" fetch_daily.py
    "$PY" repair_capped.py
    "$PY" fetch_db.py
    "$PY" prepare_prices.py
    ;;
  models)
    "$PY" run.py panel
    "$PY" run.py train
    "$PY" run.py prepare
    "$PY" run.py select
    "$PY" run.py evaluate
    ;;
  results)
    "$PY" prepare_actions.py
    "$PY" replay.py minute_price
    "$PY" replay.py analyze
    "$PY" analyze_primary.py
    "$PY" risk.py
    "$PY" audit.py
    "$PY" hash_d1_sources.py
    "$PY" quality_summary.py
    "$PY" publish_tables.py
    "$PY" manifest_final.py
    ;;
  sensitivity)
    PYTHONPATH="$PWD" "$PY" rejected_strict_extrema/prepare_actions.py
    PYTHONPATH="$PWD" "$PY" rejected_strict_extrema/replay.py minute_price
    PYTHONPATH="$PWD" "$PY" rejected_strict_extrema/replay.py analyze
    PYTHONPATH="$PWD" "$PY" rejected_strict_extrema/analyze_primary.py
    ;;
  *)
    echo "usage: ./run_backtest.sh {data|models|results|sensitivity}" >&2
    exit 2
    ;;
esac
