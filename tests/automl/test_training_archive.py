"""Training archive contract: immutable packages and verifiable file lists."""
import hashlib
from pathlib import Path

import pytest

from scripts.archive_automl_training import CATEGORIES, stage_package, verify_package


def package_at(path: Path, content: bytes) -> Path:
    path.mkdir()
    (path / "training.csv.gz").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (path / "SHA256SUMS").write_text(f"{digest}  training.csv.gz\n")
    return path


def test_stage_is_verified_and_repeatable(tmp_path: Path) -> None:
    source = package_at(tmp_path / "source", b"rows")
    root = tmp_path / "mirror"
    first = stage_package(source, root, CATEGORIES[0], "20261013_X6_test")
    second = stage_package(source, root, CATEGORIES[0], "20261013_X6_test")
    assert first == second
    assert first[2] == 1
    assert all((root / category).is_dir() for category in CATEGORIES)


def test_changed_package_cannot_overwrite_experiment(tmp_path: Path) -> None:
    root = tmp_path / "mirror"
    stage_package(package_at(tmp_path / "a", b"first"), root, CATEGORIES[0], "experiment")
    with pytest.raises(ValueError, match="different content"):
        stage_package(package_at(tmp_path / "b", b"second"), root, CATEGORIES[0], "experiment")


def test_tampered_or_unlisted_file_is_rejected(tmp_path: Path) -> None:
    source = package_at(tmp_path / "source", b"rows")
    (source / "extra.txt").write_text("unlisted")
    with pytest.raises(ValueError, match="file list differs"):
        verify_package(source)
    (source / "extra.txt").unlink()
    (source / "training.csv.gz").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_package(source)


def test_parent_path_in_manifest_is_rejected(tmp_path: Path) -> None:
    source = package_at(tmp_path / "source", b"rows")
    (source / "SHA256SUMS").write_text(f"{'0' * 64}  ../outside\n")
    with pytest.raises(ValueError, match="Invalid package path"):
        verify_package(source)
