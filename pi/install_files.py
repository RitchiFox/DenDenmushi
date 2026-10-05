"""Validate the complete Python payload, then replace and fsync each file."""
import hashlib
import os
from pathlib import Path
import tempfile

PAYLOAD = ("device_history.py", "waiting.py", "wifi_audio.py", "server.py", "audio.py", "quiet.py", "bluetooth_diagnostics.py",
           "bluetooth_sco.py", "servos.py", "servo_pwm.py", "servo_pwm_setup.py", "provision.py", "provision_core.py",
           "provision_backend.py", "provision_admin.py", "install_files.py")


def install_payload(source, destination):
    # Validate everything before replacing the first installed file.
    contents = {name: (source / name).read_bytes() for name in PAYLOAD}
    for name, data in contents.items():
        if not data.strip():
            raise ValueError("Empty installation payload: " + name)
        compile(data, name, "exec")
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name, data in contents.items():
            descriptor, temporary = tempfile.mkstemp(prefix=".install-", dir=destination)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    os.fchmod(output.fileno(), 0o644)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination / name)
                os.fsync(directory)
                installed = (destination / name).read_bytes()
                if hashlib.sha256(installed).digest() != hashlib.sha256(data).digest():
                    raise OSError("Installation verification failed: " + name)
            finally:
                Path(temporary).unlink(missing_ok=True)
    finally:
        os.close(directory)


if __name__ == "__main__":
    install_payload(Path(__file__).resolve().parent, Path("/opt/denden-demo"))
    print("Python payload written and verified.")
