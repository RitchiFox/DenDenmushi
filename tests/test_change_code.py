import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
from change_code import change_code, read_request


class ChangeCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "provision.json"
        self.original = b'{"user":"owner", "code":"00112233445566778899aabbccddeeff", "other":"kept"}\n'
        self.path.write_bytes(self.original)
        self.calls = []

    def test_changes_only_code_and_restarts(self):
        result = change_code(self.path, "owner", "a" * 32, lambda: self.calls.append(1))
        self.assertEqual(result, {"changed": True})
        self.assertEqual(json.loads(self.path.read_bytes()), {"user": "owner", "code": "a" * 32, "other": "kept"})
        self.assertEqual(self.calls, [1])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertFalse(list(self.path.parent.glob('.provision-*')))

    def test_restart_failure_restores_original_bytes(self):
        def restart():
            self.calls.append(1)
            if len(self.calls) == 1:
                raise RuntimeError("fake service failure")
        with self.assertRaises(RuntimeError):
            change_code(self.path, "owner", "b" * 32, restart)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(len(self.calls), 2)

    def test_wrong_account_does_not_change(self):
        with self.assertRaises(ValueError):
            change_code(self.path, "other", "b" * 32, lambda: self.calls.append(1))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.calls)

    def test_invalid_code_does_not_change(self):
        for value in [None, "", "2468", "g" * 32, "a" * 32 + "\n"]:
            with self.assertRaises(ValueError):
                change_code(self.path, "owner", value, lambda: self.calls.append(1))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.calls)

    def test_symlink_is_rejected(self):
        link = self.path.with_name("link")
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            change_code(link, "owner", "a" * 32, lambda: self.calls.append(1))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_sudo_request_with_and_without_consumed_password(self):
        request = b'DENDEN_CODE_REQUEST\n{"code":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
        self.assertEqual(read_request(request), {"code": "a" * 32})
        self.assertEqual(read_request(b"FAKE password\n" + request), read_request(request))
        with self.assertRaises(ValueError):
            read_request(b"missing boundary")


if __name__ == '__main__':
    unittest.main()
