#!/usr/bin/env bash
set -euo pipefail

release_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bootstrap="$release_dir/bootstrap"
if [[ ! -x "$bootstrap/bin/uv" ]]; then
  /usr/bin/python3 -m venv "$bootstrap"
  "$bootstrap/bin/python" -m pip install --disable-pip-version-check 'uv==0.9.14'
fi
export UV_PYTHON_INSTALL_DIR="$release_dir/python"
export UV_LINK_MODE=copy
"$bootstrap/bin/uv" python install 3.12.12 --no-bin
if [[ ! -x "$release_dir/.venv/bin/python" ]]; then
  "$bootstrap/bin/uv" venv "$release_dir/.venv" --python 3.12.12
fi
install_args=()
if [[ -d "$release_dir/wheelhouse" ]]; then
  install_args=(--no-index --find-links "$release_dir/wheelhouse")
fi
"$bootstrap/bin/uv" pip sync "$release_dir/requirements-linux-py312.txt" \
  --python "$release_dir/.venv/bin/python" "${install_args[@]}"
"$release_dir/.venv/bin/python" - <<'PY'
import sys, sklearn, numpy, scipy, pandas
actual = (sys.version_info[:2], sklearn.__version__, numpy.__version__,
          scipy.__version__, pandas.__version__)
expected = ((3, 12), '1.7.1', '2.5.2', '1.18.1', '2.3.3')
assert actual == expected, (actual, expected)
print('runtime verified:', actual)
PY
