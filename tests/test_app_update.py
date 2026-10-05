"""Exercise update replacement/rollback using temporary fake bundles only."""
import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('app_update', Path(__file__).resolve().parents[1] / 'scripts/update-app.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class UpdateTests(unittest.TestCase):
    def test_swap_retains_old_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target, staged, backup = [root / name for name in ('target', 'staged', 'backup')]
            target.mkdir(); staged.mkdir()
            (target / 'old').touch(); (staged / 'new').touch()
            updater.replace_app(target, staged, backup)
            self.assertTrue((target / 'new').exists())
            self.assertTrue((backup / 'old').exists())

    def test_failed_swap_restores_old_version(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target, staged, backup = [root / name for name in ('target', 'missing', 'backup')]
            target.mkdir(); (target / 'old').touch()
            with self.assertRaises(FileNotFoundError):
                updater.replace_app(target, staged, backup)
            self.assertTrue((target / 'old').exists())
            self.assertFalse(backup.exists())

    def test_existing_backup_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target, staged, backup = [root / name for name in ('target', 'staged', 'backup')]
            for path in (target, staged, backup): path.mkdir()
            with self.assertRaises(ValueError): updater.replace_app(target, staged, backup)
            self.assertTrue(target.exists())
            self.assertTrue(staged.exists())

    def test_rejects_other_app_and_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'Other.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'other.app'}))
            with self.assertRaises(ValueError): updater.validate_target(app)
            link = Path(folder) / 'Link.app'
            link.symlink_to(app)
            with self.assertRaises(ValueError): updater.validate_target(link)
