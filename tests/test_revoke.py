import base64
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
from revoke_client import revoke


def key(byte):
    return "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes([byte]) * 32).decode()


class RevokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authorized_keys"

    def test_exact_keys_only_and_preserves_other_bytes(self):
        keep = ("# " + key(1) + "\r\n" + key(2) + " other owner's key ' \"\n" + key(3) + " comment " + key(1)).encode()
        removed = ('restrict,command="/bin/false",permitopen="127.0.0.1:8789" ' + key(1) + " denden-demo\n").encode()
        self.path.write_bytes(removed + keep)
        self.assertEqual(revoke(self.path, [key(1)]), {"removed": 1, "remaining": 0})
        self.assertEqual(self.path.read_bytes(), keep)

    def test_duplicates_and_options_with_spaces(self):
        self.path.write_text(key(1) + " mac\n" + 'command="echo hello" ' + key(1) + " other comment\n" + key(2))
        self.assertEqual(revoke(self.path, [key(1)]), {"removed": 2, "remaining": 0})
        self.assertEqual(self.path.read_text(), key(2))
        self.assertEqual(revoke(self.path, [key(1)]), {"removed": 0, "remaining": 0})

    def test_saved_and_current_mac_key(self):
        self.path.write_text(key(1) + "\n" + key(2) + "\n" + key(3))
        self.assertEqual(revoke(self.path, [key(1), key(2)])["removed"], 2)
        self.assertEqual(self.path.read_text(), key(3))

    def test_rejects_bad_request_without_modification(self):
        self.path.write_text(key(1))
        for keys in [[], ["ssh-ed25519 AAAA"], [key(1), key(2), key(3)]]:
            with self.assertRaises(ValueError):
                revoke(self.path, keys)
        self.assertEqual(self.path.read_text(), key(1))

    def test_symlink_rejected(self):
        actual = self.path.with_name("other")
        actual.write_text(key(1))
        self.path.symlink_to(actual)
        with self.assertRaises(OSError):
            revoke(self.path, [key(1)])
        self.assertEqual(actual.read_text(), key(1))

    def test_missing_file_is_idempotent(self):
        self.assertEqual(revoke(self.path, [key(1)]), {"removed": 0, "remaining": 0})


if __name__ == "__main__":
    unittest.main()
