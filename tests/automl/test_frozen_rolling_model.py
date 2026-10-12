"""The committed fold artifacts must reproduce their archived historical picks."""
from pathlib import Path
import shutil

import pytest

from scripts.freeze_automl_tradable_st_fold import verify


@pytest.mark.parametrize("fold, rows", [
    ("feb02", 7715),
    ("jul30", 9755),
])
def test_frozen_model_reproduces_archived_picks(fold, rows):
    directory = Path(f"docs/stock_automl_runs/20261012_frozen_rolling20_{fold}")
    assert verify(directory)["training_rows"] == rows


def test_changed_model_file_is_rejected_before_loading(tmp_path):
    source = Path("docs/stock_automl_runs/20261012_frozen_rolling20_feb02")
    target = tmp_path / "fold"
    shutil.copytree(source, target)
    (target / "model.joblib").write_bytes(b"not the archived model")
    with pytest.raises(ValueError, match="model_sha256 mismatch"):
        verify(target)
