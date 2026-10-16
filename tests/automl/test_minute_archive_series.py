from __future__ import annotations

import io
import json
import sys
import tarfile

import pytest

from scripts.inspect_automl_minute_archive import main


def test_selective_extract_keeps_only_unadjusted_series(tmp_path, monkeypatch):
    archive = tmp_path / "sample.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        for name, content in (("mk/none/2026-01-05.csv", b"time,volume\n09:31,100\n"),
                              ("mk/post/2026-01-05.csv", b"time,volume\n09:31,10\n")):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["extract", str(archive), "--output", str(output),
                                       "--series", "none"])
    main()
    assert (output / "mk/none/2026-01-05.csv").exists()
    assert not (output / "mk/post/2026-01-05.csv").exists()
    manifest = json.loads((output / "archive_manifest.json").read_text())
    assert [row["path"] for row in manifest] == ["mk/none/2026-01-05.csv"]


def test_selective_extract_still_rejects_unsafe_skipped_members(tmp_path, monkeypatch):
    archive = tmp_path / "unsafe.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        member = tarfile.TarInfo("mk/post/../../evil.csv")
        member.size = 1
        tar.addfile(member, io.BytesIO(b"x"))
    monkeypatch.setattr(sys, "argv", ["extract", str(archive), "--output",
                                       str(tmp_path / "out"), "--series", "none"])
    with pytest.raises(ValueError, match="Unsafe"):
        main()
    assert not (tmp_path / "evil.csv").exists()
