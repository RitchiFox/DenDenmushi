"""One-time root installation helper; result is returned over authenticated SSH."""
import json
import os
import pathlib
import secrets
import sys
from provision_backend import Backend

user = sys.argv[1]
directory = pathlib.Path("/etc/denden-demo")
directory.mkdir(mode=0o700, exist_ok=True)
os.chmod(directory, 0o700)
path = directory / "provision.json"
if path.exists():
    config = json.loads(path.read_text())
    if config["user"] != user:
        raise ValueError("DenDenMushi is already installed for another Pi account")
else:
    config = {"user": user, "code": secrets.token_hex(16)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)
if len(sys.argv) > 2:
    key = pathlib.Path(sys.argv[2]).read_text()
    info = Backend(user).authorize(key)
    info["code"] = config["code"]
    print("DENDEN_RESULT=" + json.dumps(info))
