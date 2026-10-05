"""Bounded, authenticated BLE setup protocol. No Bluetooth/audio pairing changes."""
import base64
import hashlib
import json
import os
import re
import threading
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SERVICE = "1dede001-7a4a-4a0a-a800-55dede000001"
CHALLENGE = "1dede002-7a4a-4a0a-a800-55dede000001"
REQUEST = "1dede003-7a4a-4a0a-a800-55dede000001"
RESPONSE = "1dede004-7a4a-4a0a-a800-55dede000001"
PREFIX = b"denden-setup-v1:"


def setup_key(code):
    code = code.replace("-", "").replace(" ", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", code):
        raise ValueError("Invalid setup code")
    return hashlib.sha256(PREFIX + code.encode("ascii")).digest()


def public_key(value):
    parts = value.strip().split()
    if len(parts) != 2 or parts[0] != "ssh-ed25519":
        raise ValueError("Only an Ed25519 public key is accepted")
    raw = base64.b64decode(parts[1], validate=True)
    if len(raw) != 51 or raw[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
        raise ValueError("Invalid public key")
    return " ".join(parts)


def wifi_values(ssid, password):
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or "\0" in ssid:
        raise ValueError("Назва Wi-Fi має містити від 1 до 32 байтів.")
    if not isinstance(password, str) or not (8 <= len(password) <= 63 and password.isascii() or re.fullmatch(r"[0-9a-fA-F]{64}", password)) or "\0" in password:
        raise ValueError("Потрібен пароль WPA2/WPA3 Personal: 8–63 ASCII-символи.")
    return ssid, password


class Session:
    def __init__(self):
        self.challenge = os.urandom(16)
        self.buffer = bytearray()
        self.response = b""
        self.nonces = set()
        self.busy = False
        self.last = time.monotonic()


class Protocol:
    def __init__(self, code, handler):
        self.cipher = AESGCM(setup_key(code))
        self.handler = handler
        self.sessions = {}
        self.lock = threading.RLock()
        self.operation = threading.Lock()

    def hello(self, peer):
        with self.lock:
            now = time.monotonic()
            self.sessions = {p: s for p, s in self.sessions.items() if now - s.last < 180}
            if peer not in self.sessions and len(self.sessions) >= 8:
                raise ValueError("Busy")
            previous = self.sessions.get(peer)
            if previous and previous.busy:
                raise ValueError("Busy")
            self.sessions[peer] = Session()
            return self.sessions[peer].challenge

    def drop(self, peer):
        with self.lock:
            self.sessions.pop(peer, None)

    def write(self, peer, chunk):
        with self.lock:
            s = self.sessions[peer]
            if s.busy or s.response or len(s.buffer) + len(chunk) > 8192 or len(s.nonces) >= 64:
                raise ValueError("Busy or message too large; reconnect")
            s.last = time.monotonic()
            s.buffer.extend(chunk)
            if not s.buffer.endswith(b"\n"):
                return
            frame = base64.b64decode(bytes(s.buffer[:-1]), validate=True)
            s.buffer.clear()
            if len(frame) < 28 or frame[:12] in s.nonces:
                raise ValueError("Invalid message")
            # Distinct directions prevent a captured reply being used as a request.
            plain = self.cipher.decrypt(frame[:12], frame[12:], PREFIX + s.challenge + b":request")
            body = json.loads(plain)
            if not isinstance(body, dict) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(body.get("id", ""))):
                raise ValueError("Invalid message")
            s.nonces.add(frame[:12])
            s.busy = True
            threading.Thread(target=self._perform, args=(peer, s, body), daemon=True).start()

    def _perform(self, peer, s, body):
        try:
            if not self.operation.acquire(blocking=False):
                raise ValueError("Інше налаштування вже триває. Спробуй ще раз.")
            try:
                reply = {"ok": True, "data": self.handler(body)}
            finally:
                self.operation.release()
        except ValueError as error:
            reply = {"ok": False, "error": str(error)}
        except Exception:
            # Never expose D-Bus settings, commands, passwords, or tracebacks.
            reply = {"ok": False, "error": "Pi не завершив операцію. Перевір мережу й повтори."}
        reply["id"] = body["id"]
        nonce = os.urandom(12)
        encoded = base64.b64encode(nonce + self.cipher.encrypt(nonce, json.dumps(reply).encode(), PREFIX + s.challenge + b":response"))
        with self.lock:
            if self.sessions.get(peer) is s:
                s.response = encoded
                s.busy = False

    def read(self, peer):
        with self.lock:
            s = self.sessions[peer]
            s.last = time.monotonic()
            if not s.response:
                return b"\x00"
            # 19 payload bytes plus marker fits the minimum ATT MTU (23).
            chunk, s.response = s.response[:19], s.response[19:]
            return bytes([1 if s.response else 2]) + chunk
