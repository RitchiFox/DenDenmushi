"""Private, device-local PIN verification for audio gain edits only.

Provision on the device with ``audio_admin.py configure --owner SERVICE_USER``.
The PIN is read from a protected terminal (or stdin with --stdin), never an
argument. Neither this module nor the installer supplies a default credential.
"""
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import stat
import tempfile
import threading
import time

PIN_PATH = Path("/var/lib/denden-audio/admin-pin.json")
ITERATIONS = 600_000
SESSION_SECONDS = 600


def valid_pin(pin):
    return isinstance(pin, str) and 4 <= len(pin) <= 64 and pin.isascii() and pin.isdigit()


def pin_record(pin):
    if not valid_pin(pin):
        raise ValueError("PIN must contain 4 to 64 ASCII digits")
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, ITERATIONS)
    return {"version": 1, "kdf": "pbkdf2-sha256", "iterations": ITERATIONS,
            "salt": salt.hex(), "digest": digest.hex()}


def write_pin(path, pin, owner=None):
    """Atomically replace the private credential, preserving no plaintext PIN."""
    record = pin_record(pin)
    path = Path(path)
    parent_exists = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_exists and owner is not None:
        os.chown(path.parent, owner[0], owner[1])
    descriptor, temporary = tempfile.mkstemp(prefix=".admin-pin-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            if owner is not None:
                os.fchown(output.fileno(), owner[0], owner[1])
            json.dump(record, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class AudioAdmin:
    def __init__(self, path=PIN_PATH, clock=time.monotonic):
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.attempts = []
        self.credential = None

    def _record(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                        or info.st_uid not in (0, os.geteuid()) or info.st_size > 4096):
                    raise ValueError("private credential required")
                record = json.loads(source.read(4097))
            if (not isinstance(record, dict)
                    or set(record) != {"version", "kdf", "iterations", "salt", "digest"}
                    or record["version"] != 1 or record["kdf"] != "pbkdf2-sha256"
                    or type(record["iterations"]) is not int or record["iterations"] != ITERATIONS
                    or not isinstance(record["salt"], str) or len(record["salt"]) != 64
                    or not isinstance(record["digest"], str) or len(record["digest"]) != 64):
                raise ValueError("invalid credential")
            salt, digest = bytes.fromhex(record["salt"]), bytes.fromhex(record["digest"])
            if len(salt) != 32 or len(digest) != 32:
                raise ValueError("invalid credential")
            credential = (salt, digest)
        except (OSError, ValueError, TypeError):
            credential = None
        if credential != self.credential:
            self.sessions.clear()
            self.credential = credential
        return credential

    def unlock(self, pin):
        with self.lock:
            credential = self._record()
            if credential is None:
                return 503, {"ok": False, "error": "admin_unconfigured"}
            now = self.clock()
            self.attempts = [attempt for attempt in self.attempts if now - attempt < 60]
            if len(self.attempts) >= 5:
                return 429, {"ok": False, "error": "admin_rate_limited",
                             "retryAfter": max(1, math.ceil(60 - (now - self.attempts[0])))}
            self.attempts.append(now)
            if not valid_pin(pin):
                return 400, {"ok": False, "error": "invalid_request"}
            salt, expected = credential
            actual = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, ITERATIONS)
            if not hmac.compare_digest(actual, expected):
                return 401, {"ok": False, "error": "admin_invalid_pin"}
            self.sessions = {token: expiry for token, expiry in self.sessions.items() if expiry > now}
            if len(self.sessions) >= 8:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[token] = self.clock() + SESSION_SECONDS
            return 200, {"ok": True, "token": token, "expiresIn": SESSION_SECONDS}

    def authorized(self, token):
        with self.lock:
            if self._record() is None or not isinstance(token, str) or not 32 <= len(token) <= 128:
                return False
            now = self.clock()
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            return token in self.sessions

    def revoke(self, token):
        with self.lock:
            self.sessions.pop(token, None)


def main():
    import argparse
    import getpass
    import pwd
    import sys
    parser = argparse.ArgumentParser(description="Configure the private audio admin PIN on this device")
    parser.add_argument("action", choices=["configure"])
    parser.add_argument("--path", type=Path, default=PIN_PATH)
    parser.add_argument("--owner", help="Service account that must be able to read the credential")
    parser.add_argument("--stdin", action="store_true", help="Read one PIN line from protected standard input")
    args = parser.parse_args()
    if args.stdin:
        pin = sys.stdin.readline(66).rstrip("\r\n")
    else:
        pin = getpass.getpass("Audio admin PIN: ")
        if pin != getpass.getpass("Confirm PIN: "):
            parser.error("PIN confirmation does not match")
    owner = None
    if args.owner:
        account = pwd.getpwnam(args.owner)
        owner = (account.pw_uid, account.pw_gid)
    try:
        write_pin(args.path, pin, owner)
    except ValueError as error:
        parser.error(str(error))
    print("Audio admin credential configured.")


if __name__ == "__main__":
    main()
