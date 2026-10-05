import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
from audio_admin import AudioAdmin, SESSION_SECONDS, pin_record, write_pin
from server import Camera, handler_for


class CredentialFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = pin_record("246813")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "credential.json"
        self.path.write_text(json.dumps(self.record))
        self.path.chmod(0o600)
        self.now = 1000.0
        self.admin = AudioAdmin(self.path, clock=lambda: self.now)


class AudioAdminTests(CredentialFixture):
    def test_only_matching_pin_issues_short_lived_memory_session(self):
        self.assertEqual(self.admin.unlock("135790")[0], 401)
        status, result = self.admin.unlock("246813")
        self.assertEqual(status, 200)
        self.assertEqual(result["expiresIn"], SESSION_SECONDS)
        self.assertTrue(self.admin.authorized(result["token"]))
        self.assertFalse(self.admin.authorized("x" * 43))
        self.assertFalse(AudioAdmin(self.path).authorized(result["token"]))
        self.now += SESSION_SECONDS
        self.assertFalse(self.admin.authorized(result["token"]))

    def test_revoke_and_rotation_reject_existing_session(self):
        token = self.admin.unlock("246813")[1]["token"]
        self.admin.revoke(token)
        self.assertFalse(self.admin.authorized(token))
        token = self.admin.unlock("246813")[1]["token"]
        write_pin(self.path, "135790")
        self.assertFalse(self.admin.authorized(token))
        self.assertEqual(self.admin.unlock("246813")[0], 401)
        self.assertEqual(self.admin.unlock("135790")[0], 200)

    def test_rate_limit_counts_all_attempts_and_recovers(self):
        for _ in range(5):
            self.assertEqual(self.admin.unlock("135790")[0], 401)
        status, result = self.admin.unlock("246813")
        self.assertEqual(status, 429)
        self.assertEqual(result["retryAfter"], 60)
        self.now += 60
        self.assertEqual(self.admin.unlock("246813")[0], 200)

    def test_missing_invalid_public_and_symlink_credentials_fail_closed(self):
        self.path.unlink()
        self.assertEqual(self.admin.unlock("246813")[1]["error"], "admin_unconfigured")
        self.path.write_text("{}")
        self.path.chmod(0o600)
        self.assertEqual(self.admin.unlock("246813")[0], 503)
        self.path.write_text(json.dumps(self.record))
        self.path.chmod(0o644)
        self.assertEqual(self.admin.unlock("246813")[0], 503)
        target = self.path.with_suffix(".private")
        self.path.rename(target)
        target.chmod(0o600)
        self.path.symlink_to(target)
        self.assertEqual(self.admin.unlock("246813")[0], 503)

    def test_pin_format_and_private_atomic_write(self):
        for pin in (None, True, 246813, "", "123", "x" * 64, "１２３４", "1" * 65):
            self.assertEqual(self.admin.unlock(pin)[0], 400)
            self.now += 61
        write_pin(self.path, "135790")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(os.listdir(self.path.parent)), {"credential.json"})
        self.assertEqual(set(json.loads(self.path.read_text())), {"version", "kdf", "iterations", "salt", "digest"})
        self.assertEqual(self.admin.unlock("135790")[0], 200)


class AudioAdminHTTPTests(CredentialFixture):
    def setUp(self):
        super().setUp()
        self.calls = []
        owner = self
        class Audio:
            def levels(self): return {"ok": True, "output": {"db": -10}}
            def set_level(self, kind, db, muted):
                owner.calls.append((kind, db, muted))
                return self.levels()
            def connect(self, address, mode):
                owner.calls.append((address, mode))
                return {"ok": True}
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(Camera(), audio=Audio(), admin=self.admin))
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.http.server_port

    def tearDown(self):
        self.http.shutdown()
        self.thread.join()
        self.http.server_close()

    def request(self, path, body=None, token=None, headers=None, method=None):
        headers = {"X-DenDen-Client": "desktop-v1"} if headers is None else headers
        if token is not None:
            headers["X-DenDen-Audio-Admin"] = token
        request = urllib.request.Request(self.base + path, headers=headers,
            data=None if body is None else json.dumps(body).encode(),
            method=method or ("GET" if body is None else "POST"))
        try:
            with urllib.request.urlopen(request, timeout=5) as reply:
                return reply.status, json.load(reply)
        except urllib.error.HTTPError as reply:
            return reply.code, json.load(reply)

    def test_authorization_protects_writes_but_not_routing_and_status(self):
        payload = {"kind": "input", "db": -9, "muted": False}
        self.assertIn("audio-admin-v1", self.request("/status")[1]["features"])
        self.assertEqual(self.request("/audio/levels")[0], 200)
        self.assertEqual(self.request("/audio/level", payload)[0], 401)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request("/audio/connect", {"address": "example", "mode": "call"})[0], 200)
        status, result = self.request("/audio/admin/unlock", {"pin": "246813"})
        self.assertEqual(status, 200)
        token = result["token"]
        self.assertEqual(self.request("/audio/level", payload, token)[0], 200)
        self.assertEqual(self.calls[-1], ("input", -9, False))
        self.assertEqual(self.request("/audio/admin/lock", token=token, method="POST")[0], 200)
        self.assertEqual(self.request("/audio/level", payload, token)[0], 401)

    def test_unlock_browser_schema_and_attempt_boundaries(self):
        for headers in ({}, {"X-DenDen-Client": "desktop-v1", "Origin": "https://example.com"}):
            self.assertEqual(self.request("/audio/admin/unlock", {"pin": "246813"}, headers=headers)[0], 403)
        for body in ({}, [], {"pin": "246813", "extra": True}, {"pin": "0" * 257}):
            self.assertEqual(self.request("/audio/admin/unlock", body)[0], 400)
        self.assertEqual(self.admin.attempts, [])
        self.assertEqual(self.request("/audio/admin/unlock", {"pin": "135790"})[0], 401)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
