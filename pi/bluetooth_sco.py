"""One bounded runtime SCO transport repair for the verified DenDenMushi Pi.

The installer must stop its managed call service before invoking this script.
The fixed --prestart mode waits briefly for boot readiness and is run by systemd
before the unprivileged audio service starts, never as a background repair loop.
No power cycle, controller reset, address change or persistent setting is used.
"""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import bluetooth_diagnostics as diagnostic


EXPECTED_ADDRESS = "B8:27:EB:93:81:C2"
ORIGINAL_PARAMETERS = (0, 2, 0, 1, 1)
TARGET_PARAMETERS = (1, 2, 0, 1, 1)
WRITE_COMMAND = ("/usr/bin/hcitool", "-i", "hci0", "cmd", "0x3f", "0x001c",
                 "0x01", "0x02", "0x00", "0x01", "0x01")
ROLLBACK_COMMAND = ("/usr/bin/hcitool", "-i", "hci0", "cmd", "0x3f", "0x001c",
                    "0x00", "0x02", "0x00", "0x01", "0x01")
CONTROLLER_PATH = "/sys/class/bluetooth/hci0"
REPORT_PATH = "/opt/denden-demo/bluetooth-sco-repair.json"
READINESS_SECONDS = 15
TRANSIENT_READ_ERRORS = {"adapter_unverified", "command_failed", "command_timeout", "response_unverified"}


def probe_when_ready(runner=subprocess.run, read_bytes=None, exists=None,
                     monotonic=time.monotonic, sleep=time.sleep):
    """Wait for sysfs enumeration and a usable read; never power the radio on.

    Starting bluetooth.service does not guarantee the UART firmware/controller
    has finished initialization. Every probe retains its own five-second tool
    timeout; one last in-flight probe can exceed the readiness window by ten
    seconds. The service startup deadline also accommodates repair/rollback.
    """
    exists = exists or (lambda path: Path(path).exists())
    deadline = monotonic() + READINESS_SECONDS
    latest = {"outcome": "inconclusive", "adapter": {"name": "hci0"},
              "error": "controller_not_ready"}
    for _ in range(31):
        if exists(CONTROLLER_PATH):
            latest = diagnostic.probe(runner=runner, read_bytes=read_bytes)
            if latest.get("outcome") == "read":
                return latest
            if latest.get("adapter", {}).get("address") not in (None, EXPECTED_ADDRESS):
                return latest
            if latest.get("error") not in TRANSIENT_READ_ERRORS:
                return latest
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(0.5, remaining))
    return latest


def parse_write_response(output, expected_parameters):
    if not isinstance(output, str) or len(output) > 4096:
        raise diagnostic.DiagnosticError("write_response_unverified")
    match = re.fullmatch(
        r"\s*< HCI Command: ogf 0x3f, ocf 0x001c, plen 5\s+"
        r"((?:[0-9A-Fa-f]{2}\s+){4}[0-9A-Fa-f]{2})\s+"
        r"> HCI Event: 0x0e plen 4\s+"
        r"((?:[0-9A-Fa-f]{2}\s+){3}[0-9A-Fa-f]{2})\s*", output)
    if match is None or tuple(bytes.fromhex(match.group(1))) != expected_parameters:
        raise diagnostic.DiagnosticError("write_response_unverified")
    event = bytes.fromhex(match.group(2))
    if event[1:3] != b"\x1c\xfc":
        raise diagnostic.DiagnosticError("write_response_unverified")
    if event[3] != 0:
        raise diagnostic.DiagnosticError("controller_write_rejected")


def _write(runner, command, parameters):
    try:
        parse_write_response(diagnostic._run(runner, command), parameters)
        return {"outcome": "acknowledged"}
    except diagnostic.DiagnosticError as exc:
        return {"outcome": "uncertain", "error": str(exc)}
    except Exception:
        # A write might already have reached the controller. The caller must
        # still attempt independent readback after every failure here.
        return {"outcome": "uncertain", "error": "write_failed"}


def _same_adapter(observation, adapter):
    # probe only publishes a full adapter after both DT and UART validation.
    return observation.get("adapter") == adapter


def _verified_parameters(observation, adapter, parameters):
    return (_same_adapter(observation, adapter) and observation.get("outcome") == "read"
            and observation.get("parameters") == list(parameters))


def repair(runner=subprocess.run, read_bytes=None, wait_for_ready=False):
    result = {"schemaVersion": 1,
              "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "outcome": "inconclusive", "runtimeOnly": True}
    before = (probe_when_ready(runner=runner, read_bytes=read_bytes) if wait_for_ready
              else diagnostic.probe(runner=runner, read_bytes=read_bytes))
    result["before"] = before
    adapter = before.get("adapter", {})
    result["adapter"] = adapter
    if before.get("outcome") != "read":
        result["error"] = "initial_read_unverified"
        return result
    result["originalParameters"] = before["parameters"]
    if adapter.get("address") != EXPECTED_ADDRESS:
        result["error"] = "unexpected_adapter"
        return result
    if before["parameters"] == list(TARGET_PARAMETERS):
        result["outcome"] = "already_configured"
        return result
    if before["parameters"] != list(ORIGINAL_PARAMETERS):
        result["error"] = "unsupported_parameters"
        return result

    result["write"] = _write(runner, WRITE_COMMAND, TARGET_PARAMETERS)
    after = diagnostic.probe(runner=runner, read_bytes=read_bytes)
    result["afterWrite"] = after
    if _verified_parameters(after, adapter, TARGET_PARAMETERS):
        result["outcome"] = "repaired"
        return result

    result["error"] = "repair_unverified"
    if not _same_adapter(after, adapter):
        # Never issue the rollback to a changed or no longer verified device.
        result["rollback"] = {"outcome": "skipped", "error": "adapter_unverified"}
        return result
    result["rollback"] = _write(runner, ROLLBACK_COMMAND, ORIGINAL_PARAMETERS)
    restored = diagnostic.probe(runner=runner, read_bytes=read_bytes)
    result["afterRollback"] = restored
    if _verified_parameters(restored, adapter, ORIGINAL_PARAMETERS):
        result["outcome"] = "rolled_back"
    else:
        result["error"] = "rollback_unverified"
    return result


def write_report(result, path=REPORT_PATH):
    """Atomically replace only the fixed root-owned metadata report."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".bluetooth-sco-",
                                         dir=Path(path).parent, delete=False) as output:
            temporary = output.name
            json.dump(result, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fchmod(output.fileno(), 0o644)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    prestart = argv == ["--prestart"]
    if argv and not prestart:
        result = {"schemaVersion": 1, "outcome": "inconclusive", "error": "unsupported_arguments"}
    else:
        try:
            result = repair(wait_for_ready=True) if prestart else repair()
        except Exception:
            result = {"schemaVersion": 1, "outcome": "inconclusive", "error": "repair_failed"}
    if prestart:
        result["invocation"] = "service_prestart"
        try:
            write_report(result)
        except OSError:
            result["reportError"] = "report_write_failed"
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
