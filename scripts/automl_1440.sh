#!/bin/zsh
set -euo pipefail
cd /Volumes/ext/fin_agent
sklearn171_site=/Volumes/ext/fin_agent/outputs/stock_automl/runtime/sklearn_171
if [[ ! -d "$sklearn171_site/sklearn" ]]; then
  print -u2 "缺少固定的 scikit-learn 1.7.1 环境：$sklearn171_site"
  exit 1
fi
export PYTHONPATH="$sklearn171_site${PYTHONPATH:+:$PYTHONPATH}"
/Volumes/ext/fin_agent/.venv/bin/python -c 'import sklearn,numpy,scipy,pandas; actual=(sklearn.__version__,numpy.__version__,scipy.__version__,pandas.__version__); expected=("1.7.1","2.5.2","1.18.1","2.3.3"); assert actual == expected, (actual,expected)'
if [[ "${1:-}" == prepare ]]; then
  /Volumes/ext/fin_agent/.venv/bin/python -m scripts.check_backfill_automl_previous_day --execute
fi
exec /Volumes/ext/fin_agent/.venv/bin/python -m scripts.run_automl_1440_strategy "$@"
