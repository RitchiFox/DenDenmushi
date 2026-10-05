"""Remove only the supplied Mac public keys, as the owning SSH user."""
import base64
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import sys


def canonical_key(value):
    parts = value.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError("Expected an Ed25519 public key")
    raw = base64.b64decode(parts[1], validate=True)
    prefix = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
    if len(raw) != 51 or not raw.startswith(prefix):
        raise ValueError("Invalid public key")
    return "ssh-ed25519 " + parts[1]


def line_key(line):
    # Read only the key fields: comments may contain arbitrary quotes or keys.
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    try:
        first = next(lexer, "")
        kind = first if first == "ssh-ed25519" else next(lexer, "")
        if kind == "ssh-ed25519":
            return canonical_key(kind + " " + next(lexer, ""))
    except (ValueError, TypeError):
        pass
    return None


def revoke(path, keys):
    targets = {canonical_key(key) for key in keys}
    if not targets or len(targets) > 2:
        raise ValueError("Expected one Mac's current/saved key")
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {"removed": 0, "remaining": 0}
    with os.fdopen(fd, "r+b") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Unexpected authorized_keys file")
        original = file.read(1024 * 1024 + 1)
        if len(original) > 1024 * 1024:
            raise ValueError("authorized_keys too large")
        lines = original.splitlines(keepends=True)
        kept = [line for line in lines if line_key(line.decode("utf-8", "replace")) not in targets]
        removed = len(lines) - len(kept)
        if removed:
            file.seek(0)
            file.write(b"".join(kept))
            file.truncate()
            file.flush()
            os.fsync(file.fileno())
        file.seek(0)
        remaining = sum(line_key(line.decode("utf-8", "replace")) in targets for line in file.readlines())
        if remaining:
            raise RuntimeError("Key removal could not be verified")
        return {"removed": removed, "remaining": remaining}


if __name__ == "__main__":
    request = json.loads(sys.stdin.buffer.read(16384))
    result = revoke(Path.home() / ".ssh" / "authorized_keys", request["publicKeys"])
    print("DENDEN_RESET=" + json.dumps(result))
