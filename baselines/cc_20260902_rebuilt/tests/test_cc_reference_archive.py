"""Offline archive checks; all generated artifacts stay in temporary directories."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest

_TEST_BASE = Path(__file__).resolve().parents[1]
if (_TEST_BASE / "history").is_dir() and (_TEST_BASE / "tools").is_dir():
    _spec = importlib.util.spec_from_file_location(
        "sealed_cc_reference_archive", _TEST_BASE / "tools/build_cc_reference_archive.py")
    assert _spec is not None and _spec.loader is not None
    reference = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = reference
    _spec.loader.exec_module(reference)
else:
    from scripts import build_cc_reference_archive as reference


class CcReferenceArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="cc_reference_tests_")
        cls.root = Path(cls.temporary.name)
        cls.archive_dir = cls.root / "archive"
        # git ls-tree is relative to -C's working directory; a sealed copy lives
        # below baselines/, while the fixed Git source still belongs to the
        # surrounding repository/worktree (whose .git may be a file).
        cls.repo = next(
            (parent for parent in (_TEST_BASE, *_TEST_BASE.parents)
             if (parent / ".git").exists()), _TEST_BASE)
        cls.manifest = reference.build_archive(cls.repo, cls.archive_dir)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def copy_archive(self, name):
        return Path(shutil.copytree(self.archive_dir, self.root / name))

    def test_build_verify_extract_and_reproducibility(self):
        verified = reference.verify_archive(self.archive_dir)
        self.assertEqual(verified, self.manifest)
        self.assertEqual(verified["source_commit"], reference.SOURCE_COMMIT)
        self.assertEqual(verified["catalog_sha256"], reference.CATALOG_SHA256)
        self.assertIn("not an exact", verified["reconstruction_note"])
        self.assertIn("data/index_subjects.tsv", verified["files"])
        self.assertIn("src/scenarios/financial_qa/service.py", verified["files"])
        self.assertIn("frontend/package-lock.json", verified["files"])
        self.assertFalse(any(name.startswith((".env", "outputs/", "data/runtime")) for name in verified["files"]))
        self.assertEqual(set(path.name for path in self.archive_dir.iterdir()), {"manifest.json", "source.tar.gz"})
        target = self.root / "extracted"
        target.mkdir()
        reference.extract_archive(self.archive_dir, target)
        for name, entry in verified["files"].items():
            self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), entry["sha256"])
        second = self.root / "second_build"
        reference.build_archive(self.repo, second)
        for name in ("source.tar.gz", "manifest.json"):
            self.assertEqual((self.archive_dir / name).read_bytes(), (second / name).read_bytes())

    def test_rejects_archive_digest_tamper_before_extract(self):
        archive = self.copy_archive("tampered_archive")
        with (archive / "source.tar.gz").open("ab") as handle:
            handle.write(b"tamper")
        target = self.root / "must_not_be_created"
        with self.assertRaisesRegex(reference.ArchiveError, "Archive SHA256 mismatch"):
            reference.extract_archive(archive, target)
        self.assertFalse(target.exists())

    def test_rejects_file_digest_tamper(self):
        archive = self.copy_archive("tampered_manifest")
        manifest_path = archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["requirements.txt"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(reference.ArchiveError, "Source SHA256 mismatch"):
            reference.verify_archive(archive)

    def test_rejects_nonempty_target_and_existing_archive(self):
        target = self.root / "nonempty"
        target.mkdir()
        (target / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(reference.ArchiveError, "new or empty"):
            reference.extract_archive(self.archive_dir, target)
        self.assertEqual((target / "keep.txt").read_text(), "keep")
        with self.assertRaisesRegex(reference.ArchiveError, "already exist"):
            reference.build_archive(self.repo, self.archive_dir)

    def test_rejects_traversal_and_links_even_if_archive_digest_is_updated(self):
        for index, (name, member_type) in enumerate((("../escape", tarfile.REGTYPE),
                                                    ("requirements.txt", tarfile.SYMTYPE))):
            archive = self.copy_archive(f"unsafe_{index}")
            manifest_path = archive / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            archive_bytes = io.BytesIO()
            with tarfile.open(fileobj=archive_bytes, mode="w:gz") as handle:
                member = tarfile.TarInfo(name)
                member.type = member_type
                if member_type == tarfile.SYMTYPE:
                    member.linkname = "../../escape"
                handle.addfile(member)
            content = archive_bytes.getvalue()
            (archive / "source.tar.gz").write_bytes(content)
            manifest["source_archive"]["sha256"] = hashlib.sha256(content).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(reference.ArchiveError, "Unsafe or unexpected"):
                reference.verify_archive(archive)

    def test_secret_scan_reports_only_key_and_location(self):
        secret = "SENSITIVE_CREDENTIAL_EXAMPLE"
        findings = reference.scan_secrets("src/example.py", f'password = "{secret}"\n'.encode())
        self.assertEqual(findings, ["src/example.py:1:password"])
        self.assertNotIn(secret, str(findings))
        self.assertEqual(reference.scan_secrets("src/example.py", b'password = os.getenv("DB_PASSWORD", "")\n'), [])

    def test_appledouble_is_excluded_at_every_level(self):
        for name in ("._requirements.txt", "src/._service.py", "frontend/._src/App.tsx"):
            self.assertFalse(reference._allowed_path(name))


if __name__ == "__main__":
    unittest.main()
