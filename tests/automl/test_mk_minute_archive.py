import hashlib
import io
import json
import tarfile

import pandas as pd
import pytest

from scripts.compact_mk_minute_archive import compact
from scripts.compact_mk_2025_tar_archive import compact_tar_archives
from scripts.mk_minute_archive import day_file, day_files, trading_day


def test_compact_round_trip_and_resume(tmp_path):
    root = tmp_path / "source"
    source = root / "mk" / "none" / "2026-01-05.csv"
    source.parent.mkdir(parents=True)
    source.write_text("time,symbol,close\n" +
                      "2026-01-05 09:31:00,000001.SZ,11.42\n" * 100)
    original = source.read_bytes()
    (root / "archive_manifest.json").write_text(json.dumps([{
        "path": "mk/none/2026-01-05.csv", "bytes": len(original),
        "sha256": hashlib.sha256(original).hexdigest(),
    }]))
    output = tmp_path / "compact"
    result = compact((root,), output)
    assert result["days"] == 1
    assert result["compressed_bytes"] < result["original_bytes"]
    assert not source.exists()
    paths = day_files((output,))
    assert len(paths) == 1 and trading_day(paths[0]) == "2026-01-05"
    assert day_file(output, "2026-01-05") == paths[0]
    assert pd.read_csv(paths[0]).close.tolist() == [11.42] * 100
    assert compact((root,), output)["files"] == result["files"]


def test_missing_or_duplicate_day_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        day_file(tmp_path, "2026-01-05")
    for suffix in (".csv", ".csv.zst"):
        (tmp_path / f"2026-01-05{suffix}").write_bytes(b"x")
    with pytest.raises(ValueError, match="Duplicate"):
        day_files((tmp_path,))


def test_compact_2025_tar_stream_round_trip_and_resume(tmp_path):
    archive = tmp_path / "mk202501_202506.tar.xz"
    content = ("time,symbol,open,high,low,close,volume,turnover\n" +
               "2025-01-02 09:31:00,000001.SZ,1,2,1,2,100,200\n" * 100).encode()
    with tarfile.open(archive, "w:xz") as tar:
        for kind in ("none", "post"):
            info = tarfile.TarInfo(f"mk/{kind}/2025-01-02.csv")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    output = tmp_path / "compact"
    args = ((archive, "2025-01-01", "2025-06-30"),)
    result = compact_tar_archives(args, output)
    assert result["days"] == 1
    assert result["files_count"] == 1
    assert result["files"][0]["original_sha256"] == hashlib.sha256(content).hexdigest()
    assert pd.read_csv(day_file(output, "2025-01-02")).shape == (100, 8)
    assert [trading_day(path) for path in day_files((output,))] == ["2025-01-02"]
    assert not (output / "post").exists()
    assert compact_tar_archives(args, output) == result
