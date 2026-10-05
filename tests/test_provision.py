import base64
import json
import hashlib
import os
import pathlib
import subprocess
import sys
import time
import unittest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "pi"))
from provision_core import Protocol, PREFIX, public_key, setup_key, wifi_values

CODE = "00112233445566778899aabbccddeeff"


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.protocol = Protocol(CODE, lambda request: self.calls.append(request) or {"hello": "Pi → Mac"})
        self.challenge = self.protocol.hello("mac")
        self.cipher = AESGCM(setup_key(CODE))

    def frame(self, body=None, key=None, challenge=None):
        nonce = os.urandom(12)
        body = body or {"id": "test-1", "op": "info"}
        cipher = AESGCM(key) if key else self.cipher
        return base64.b64encode(nonce + cipher.encrypt(nonce, json.dumps(body).encode(), PREFIX + (challenge or self.challenge) + b":request")) + b"\n"

    def collect(self):
        data = b""
        for _ in range(300):
            value = self.protocol.read("mac")
            data += value[1:]
            if value[0] == 2:
                return base64.b64decode(data)
            time.sleep(0.002)
        self.fail("No response")

    def test_chunked_encrypted_roundtrip(self):
        frame = self.frame()
        for i in range(0, len(frame), 20):
            self.protocol.write("mac", frame[i:i+20])
        reply = self.collect()
        value = json.loads(self.cipher.decrypt(reply[:12], reply[12:], PREFIX + self.challenge + b":response"))
        self.assertEqual(value, {"ok": True, "id": "test-1", "data": {"hello": "Pi → Mac"}})
        self.assertEqual(len(self.calls), 1)

    def test_wrong_code_cannot_call_handler(self):
        with self.assertRaises(Exception):
            self.protocol.write("mac", self.frame(key=os.urandom(32)))
        self.assertEqual(self.calls, [])

    def test_modified_ciphertext_cannot_call_handler(self):
        raw = bytearray(base64.b64decode(self.frame()))
        raw[-1] ^= 1
        with self.assertRaises(Exception):
            self.protocol.write("mac", base64.b64encode(raw) + b"\n")
        self.assertEqual(self.calls, [])

    def test_replay_is_rejected(self):
        frame = self.frame()
        self.protocol.write("mac", frame)
        self.collect()
        with self.assertRaises(ValueError):
            self.protocol.write("mac", frame)
        self.assertEqual(len(self.calls), 1)

    def test_reconnect_invalidates_captured_request(self):
        old = self.frame()
        self.protocol.drop("mac")
        self.protocol.hello("mac")
        with self.assertRaises(Exception):
            self.protocol.write("mac", old)
        self.assertFalse(self.calls)

    def test_other_peer_cannot_use_request(self):
        self.protocol.hello("other")
        with self.assertRaises(Exception):
            self.protocol.write("other", self.frame())
        self.assertFalse(self.calls)

    def test_bounded_sessions_and_messages(self):
        for i in range(7):
            self.protocol.hello(str(i))
        with self.assertRaises(ValueError):
            self.protocol.hello("ninth")
        with self.assertRaises(ValueError):
            self.protocol.write("mac", b"A" * 8193)

    def test_direction_prevents_response_reflection(self):
        self.protocol.write("mac", self.frame())
        reply = self.collect()
        with self.assertRaises(Exception):
            self.protocol.write("mac", base64.b64encode(reply) + b"\n")
        self.assertEqual(len(self.calls), 1)

    def test_unexpected_error_is_not_leaked(self):
        def bad(request):
            raise RuntimeError("secret must not escape")
        self.protocol.handler = bad
        self.protocol.write("mac", self.frame())
        reply = self.collect()
        plain = self.cipher.decrypt(reply[:12], reply[12:], PREFIX + self.challenge + b":response")
        self.assertNotIn(b"secret", plain)
        self.assertFalse(json.loads(plain)["ok"])

    def test_key_and_wifi_validation(self):
        raw = b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(32)
        key = "ssh-ed25519 " + base64.b64encode(raw).decode()
        self.assertEqual(public_key(key), key)
        for invalid in [key + "\ncommand=evil", 'command="evil" ' + key, "ssh-ed25519 YWJj", "ssh-rsa " + key.split()[1]]:
            with self.assertRaises(ValueError):
                public_key(invalid)
        self.assertEqual(wifi_values("Тестова", "password1"), ("Тестова", "password1"))
        for ssid, password in [("", "password1"), ("x" * 33, "password1"), ("valid", "short"), ("valid", "x\0xxxxxxx")]:
            with self.assertRaises(ValueError):
                wifi_values(ssid, password)

    def test_swift_python_interoperability(self):
        binary = os.environ.get("DENDEN_SETUP_SMOKE")
        if not binary:
            self.skipTest("Run scripts/test-setup.sh for Swift interoperability")
        self.protocol.sessions["mac"].challenge = bytes(range(16))
        frame = subprocess.check_output([binary, "seal"]).strip() + b"\n"
        for i in range(0, len(frame), 20):
            self.protocol.write("mac", frame[i:i+20])
        reply = self.collect()
        subprocess.run([binary, "open", base64.b64encode(reply).decode()], check=True, stdout=subprocess.DEVNULL)

    def test_password_swift_python_interoperability(self):
        binary = os.environ.get("DENDEN_SETUP_SMOKE")
        if not binary:
            self.skipTest("Run scripts/test-setup.sh for Swift interoperability")
        derived = hashlib.pbkdf2_hmac("sha256", b"4826", b"denden-user-password-v1:", 600000, 16).hex()
        self.protocol = Protocol(derived, lambda _: {"hello": "Pi → Mac"})
        self.protocol.hello("mac")
        self.protocol.sessions["mac"].challenge = bytes(range(16))
        frame = subprocess.check_output([binary, "password-seal"]).strip() + b"\n"
        for i in range(0, len(frame), 20):
            self.protocol.write("mac", frame[i:i+20])
        reply = self.collect()
        subprocess.run([binary, "password-open", base64.b64encode(reply).decode()], check=True, stdout=subprocess.DEVNULL)
        self.protocol.hello("mac")
        with self.assertRaises(Exception):
            self.protocol.write("mac", self.frame(challenge=self.protocol.sessions["mac"].challenge))


if __name__ == "__main__":
    unittest.main()
