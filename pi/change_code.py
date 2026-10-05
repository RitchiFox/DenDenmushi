"""Owner-only setup-code change, invoked via authenticated SSH and sudo."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix=".provision-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def change_code(path, user, code, restart):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9a-f]{32}", code):
        raise ValueError("Invalid setup credential")
    lock = os.open(path.parent / ".provision.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "r+") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError("Unexpected configuration owner")
            original = file.read(16385)
        if len(original) > 16384:
            raise ValueError("Invalid configuration")
        config = json.loads(original)
        if not user or config.get("user") != user:
            raise ValueError("Pi service belongs to another account")
        config["code"] = code
        atomic_write(path, json.dumps(config).encode())
        try:
            restart()
        except Exception:
            atomic_write(path, original)
            try:
                restart()
            except Exception:
                pass
            raise RuntimeError("Не вдалося перезапустити Bluetooth. Попередній код відновлено.") from None
        # No code or other secrets in the response.
        return {"changed": True}


def read_request(raw):
    # sudo may consume the first password line, or leave it when auth is cached.
    # A fixed boundary keeps either case out of the JSON parser and any errors.
    _, boundary, body = raw.rpartition(b"DENDEN_CODE_REQUEST\n")
    if not boundary:
        raise ValueError("Missing request boundary")
    return json.loads(body)


def main():
    if os.geteuid() != 0 or not os.environ.get("SUDO_USER"):
        raise ValueError("Owner sudo authentication required")
    request = read_request(sys.stdin.buffer.read(16384))
    sys.path.insert(0, "/opt/denden-demo")
    from provision_backend import Backend
    user = os.environ["SUDO_USER"]
    info = Backend(user).info()
    def restart():
        subprocess.run(["/usr/bin/systemctl", "restart", "denden-setup.service"], check=True, timeout=25, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", "denden-setup.service"], check=True, timeout=5)
    result = change_code(Path("/etc/denden-demo/provision.json"), user, request.get("code"), restart)
    print("DENDEN_CHANGED=" + json.dumps(dict(info, **result)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Не вдалося підтвердити зміну. Перевір пароль користувача Pi та повтори операцію.", file=sys.stderr)
        sys.exit(1)
