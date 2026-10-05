from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
from install_files import PAYLOAD, install_payload


class InstallFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source, self.target = root / "source", root / "target"
        self.source.mkdir()
        self.target.mkdir()
        for name in PAYLOAD:
            (self.source / name).write_text('value = "new"\n')
            (self.target / name).write_text('value = "old"\n')

    def test_empty_file_rejects_entire_payload_before_replacing_anything(self):
        (self.source / PAYLOAD[-1]).write_bytes(b"")
        with self.assertRaises(ValueError): install_payload(self.source, self.target)
        for name in PAYLOAD:
            self.assertIn('"old"', (self.target / name).read_text())

    def test_verified_files_and_no_temporary_residue(self):
        install_payload(self.source, self.target)
        self.assertEqual(set(p.name for p in self.target.iterdir()), set(PAYLOAD))
        for name in PAYLOAD:
            self.assertEqual((self.source / name).read_bytes(), (self.target / name).read_bytes())
            self.assertEqual((self.target / name).stat().st_mode & 0o777, 0o644)

    def test_failed_fsync_preserves_existing_file(self):
        with patch("install_files.os.fsync", side_effect=OSError("write failed")):
            with self.assertRaises(OSError): install_payload(self.source, self.target)
        self.assertEqual(set(p.name for p in self.target.iterdir()), set(PAYLOAD))
        for name in PAYLOAD:
            self.assertIn('"old"', (self.target / name).read_text())
