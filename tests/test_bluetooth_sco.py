import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
import bluetooth_diagnostics as diagnostic
import bluetooth_sco as sco


ADAPTER = """hci0:   Type: Primary  Bus: UART
        BD Address: B8:27:EB:93:81:C2  ACL MTU: 1021:8  SCO MTU: 64:1
        UP RUNNING PSCAN
"""


def read_response(parameters):
    return ("< HCI Command: ogf 0x3f, ocf 0x001d, plen 0\n"
            "> HCI Event: 0x0e plen 9\n  01 1D FC 00 "
            + " ".join(f"{value:02X}" for value in parameters) + "\n")


def write_response(parameters):
    return ("< HCI Command: ogf 0x3f, ocf 0x001c, plen 5\n  "
            + " ".join(f"{value:02X}" for value in parameters)
            + "\n> HCI Event: 0x0e plen 4\n  01 1C FC 00\n")


class BluetoothScoTests(unittest.TestCase):
    def repair(self, reads, writes=None, adapters=None, hardware=True):
        reads = iter(reads)
        writes = iter(writes if writes is not None else [write_response(sco.TARGET_PARAMETERS)])
        adapters = iter(adapters) if adapters is not None else None
        calls = []
        def runner(command, **kwargs):
            command = tuple(command)
            calls.append(command)
            self.assertEqual(kwargs["timeout"], 5)
            self.assertNotIn("shell", kwargs)
            if command == diagnostic.ADAPTER_COMMAND:
                value = next(adapters) if adapters is not None else ADAPTER
            elif command == diagnostic.READ_COMMAND:
                value = next(reads)
                if isinstance(value, (list, tuple)):
                    value = read_response(value)
            elif command in (sco.WRITE_COMMAND, sco.ROLLBACK_COMMAND):
                value = next(writes)
            else:
                self.fail(f"Unexpected command: {command}")
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(returncode=0, stdout=value, stderr="")
        def read_bytes(path):
            if not hardware:
                return b"unknown\0"
            return (b"raspberrypi,3-model-b\0brcm,bcm2837\0" if path == diagnostic.MODEL_COMPATIBLE
                    else b"brcm,bcm43438-bt\0")
        result = sco.repair(runner=runner, read_bytes=read_bytes)
        return result, calls

    def test_exact_supported_state_is_changed_once_and_independently_verified(self):
        result, calls = self.repair([sco.ORIGINAL_PARAMETERS, sco.TARGET_PARAMETERS])
        self.assertEqual(result["outcome"], "repaired")
        self.assertEqual(result["originalParameters"], list(sco.ORIGINAL_PARAMETERS))
        self.assertTrue(result["runtimeOnly"])
        self.assertEqual(calls, [diagnostic.ADAPTER_COMMAND, diagnostic.READ_COMMAND,
                                 sco.WRITE_COMMAND, diagnostic.ADAPTER_COMMAND, diagnostic.READ_COMMAND])

    def test_already_configured_or_unknown_parameters_never_write(self):
        for parameters, outcome in ((sco.TARGET_PARAMETERS, "already_configured"),
                                    ((0, 3, 0, 1, 1), "inconclusive")):
            result, calls = self.repair([parameters])
            self.assertEqual(result["outcome"], outcome)
            self.assertEqual(calls, [diagnostic.ADAPTER_COMMAND, diagnostic.READ_COMMAND])

    def test_wrong_hardware_adapter_or_initial_read_cannot_write(self):
        result, calls = self.repair([], hardware=False)
        self.assertEqual(result["error"], "initial_read_unverified")
        self.assertEqual(calls, [])
        result, calls = self.repair([sco.ORIGINAL_PARAMETERS],
                                    adapters=[ADAPTER.replace("B8:27:EB:93:81:C2", "11:22:33:44:55:66")])
        self.assertEqual(result["error"], "unexpected_adapter")
        self.assertEqual(len(calls), 2)
        result, calls = self.repair(["unrelated event"])
        self.assertEqual(result["error"], "initial_read_unverified")
        self.assertEqual(len(calls), 2)

    def test_uncertain_write_is_success_only_after_verified_target_readback(self):
        for response in (subprocess.TimeoutExpired("private", 5), PermissionError("private"),
                         "unrelated event", write_response(sco.TARGET_PARAMETERS).replace("1C FC 00", "1C FC 01")):
            with self.subTest(response=response):
                result, calls = self.repair([sco.ORIGINAL_PARAMETERS, sco.TARGET_PARAMETERS], [response])
                self.assertEqual(result["outcome"], "repaired")
                self.assertEqual(result["write"]["outcome"], "uncertain")
                self.assertNotIn(sco.ROLLBACK_COMMAND, calls)
                self.assertNotIn("private", json.dumps(result))

    def test_failed_readback_attempts_single_fixed_rollback_and_verifies_it(self):
        for readback in (sco.ORIGINAL_PARAMETERS, (2, 2, 0, 1, 1),
                         subprocess.TimeoutExpired("private", 5), "unrelated event"):
            with self.subTest(readback=readback):
                result, calls = self.repair([sco.ORIGINAL_PARAMETERS, readback, sco.ORIGINAL_PARAMETERS],
                                            [subprocess.TimeoutExpired("private", 5),
                                             write_response(sco.ORIGINAL_PARAMETERS)])
                self.assertEqual(result["outcome"], "rolled_back")
                self.assertEqual(calls.count(sco.WRITE_COMMAND), 1)
                self.assertEqual(calls.count(sco.ROLLBACK_COMMAND), 1)
                self.assertEqual(calls[-1], diagnostic.READ_COMMAND)

    def test_rollback_timeout_still_checks_final_state_and_never_retries(self):
        for last, expected in ((sco.ORIGINAL_PARAMETERS, "rolled_back"),
                               (sco.TARGET_PARAMETERS, "inconclusive"), ("unrelated", "inconclusive")):
            result, calls = self.repair([sco.ORIGINAL_PARAMETERS, sco.ORIGINAL_PARAMETERS, last],
                                        ["unrelated", subprocess.TimeoutExpired("private", 5)])
            self.assertEqual(result["outcome"], expected)
            if expected == "inconclusive":
                self.assertEqual(result["error"], "rollback_unverified")
            self.assertEqual(calls.count(sco.ROLLBACK_COMMAND), 1)

    def test_changed_or_unverified_controller_is_never_rolled_back(self):
        for adapter in (ADAPTER.replace("B8:27:EB:93:81:C2", "11:22:33:44:55:66"),
                        ADAPTER.replace("UART", "USB")):
            result, calls = self.repair([sco.ORIGINAL_PARAMETERS, sco.ORIGINAL_PARAMETERS], adapters=[ADAPTER, adapter])
            self.assertEqual(result["outcome"], "inconclusive")
            self.assertEqual(result["rollback"], {"outcome": "skipped", "error": "adapter_unverified"})
            self.assertNotIn(sco.ROLLBACK_COMMAND, calls)

    def test_write_completion_requires_exact_echo_event_opcode_and_success(self):
        valid = write_response(sco.TARGET_PARAMETERS)
        sco.parse_write_response(valid, sco.TARGET_PARAMETERS)
        for value in (valid.replace("1C FC", "1D FC"), valid.replace("plen 4", "plen 5"),
                      valid.replace("0x0e", "0x0f"), valid.replace("  01 02", "  00 02"),
                      valid.replace("1C FC 00", "1C FC 01"), valid + "00", valid + valid):
            with self.subTest(value=value):
                with self.assertRaises(diagnostic.DiagnosticError):
                    sco.parse_write_response(value, sco.TARGET_PARAMETERS)

    def test_cli_rejects_arguments_and_returns_nonfatal_json(self):
        output = io.StringIO()
        with patch.object(sco, "repair", side_effect=AssertionError("must not write")):
            with contextlib.redirect_stdout(output):
                self.assertEqual(sco.main(["--device", "hci1"]), 0)
        self.assertEqual(json.loads(output.getvalue())["error"], "unsupported_arguments")
        with patch.object(sco, "repair", return_value={"outcome": "inconclusive"}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sco.main([]), 0)


class BluetoothScoStartupTests(unittest.TestCase):
    def fake_clock(self):
        now = [0.0]
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        return lambda: now[0], sleep, sleeps

    def test_boot_waits_for_sysfs_and_a_verified_read_without_writes(self):
        monotonic, sleep, sleeps = self.fake_clock()
        presence = iter([False, False, True, True])
        ready = {"outcome": "read", "parameters": list(sco.ORIGINAL_PARAMETERS),
                 "adapter": {"name": "hci0", "address": sco.EXPECTED_ADDRESS, "bus": "UART"}}
        with patch.object(diagnostic, "probe", side_effect=[{"outcome": "inconclusive", "error": "adapter_unverified"}, ready]) as probe:
            with patch.object(sco, "_write", side_effect=AssertionError("readiness must not write")):
                result = sco.probe_when_ready(exists=lambda path: next(presence), monotonic=monotonic, sleep=sleep)
        self.assertEqual(result, ready)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(sleeps, [0.5, 0.5, 0.5])

    def test_missing_controller_stops_at_deadline_without_opening_radio(self):
        monotonic, sleep, sleeps = self.fake_clock()
        with patch.object(diagnostic, "probe", side_effect=AssertionError("no controller")):
            result = sco.probe_when_ready(exists=lambda path: False, monotonic=monotonic, sleep=sleep)
        self.assertEqual(result["error"], "controller_not_ready")
        self.assertEqual(sum(sleeps), sco.READINESS_SECONDS)

    def test_transient_reads_are_bounded_and_hard_failures_stop_immediately(self):
        for code in ("hardware_unverified", "permission_denied", "command_unavailable"):
            monotonic, sleep, sleeps = self.fake_clock()
            with patch.object(diagnostic, "probe", return_value={"outcome": "inconclusive", "error": code}) as probe:
                result = sco.probe_when_ready(exists=lambda path: True, monotonic=monotonic, sleep=sleep)
            self.assertEqual(result["error"], code)
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(sleeps, [])
        monotonic, sleep, sleeps = self.fake_clock()
        with patch.object(diagnostic, "probe", return_value={"outcome": "inconclusive", "error": "command_failed"}) as probe:
            result = sco.probe_when_ready(exists=lambda path: True, monotonic=monotonic, sleep=sleep)
        self.assertEqual(result["error"], "command_failed")
        self.assertLessEqual(probe.call_count, 31)
        self.assertEqual(sum(sleeps), sco.READINESS_SECONDS)

    def test_startup_readiness_retains_exact_adapter_and_parameter_write_guard(self):
        observations = [
            {"outcome": "read", "parameters": list(sco.ORIGINAL_PARAMETERS),
             "adapter": {"name": "hci0", "address": "11:22:33:44:55:66", "bus": "UART"}},
            {"outcome": "read", "parameters": [0, 3, 0, 1, 1],
             "adapter": {"name": "hci0", "address": sco.EXPECTED_ADDRESS, "bus": "UART"}},
        ]
        for observation in observations:
            with patch.object(sco, "probe_when_ready", return_value=observation) as ready:
                with patch.object(sco, "_write", side_effect=AssertionError("unsupported controller/state")):
                    result = sco.repair(wait_for_ready=True)
            self.assertEqual(result["outcome"], "inconclusive")
            ready.assert_called_once()

    def test_prestart_is_explicit_waits_and_saves_only_metadata_nonfatally(self):
        output = io.StringIO()
        with patch.object(sco, "repair", return_value={"outcome": "already_configured"}) as repair:
            with patch.object(sco, "write_report", side_effect=PermissionError("private path")) as save:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(sco.main(["--prestart"]), 0)
        repair.assert_called_once_with(wait_for_ready=True)
        save.assert_called_once()
        result = json.loads(output.getvalue())
        self.assertEqual(result["invocation"], "service_prestart")
        self.assertEqual(result["reportError"], "report_write_failed")
        self.assertNotIn("private", output.getvalue())

    def test_report_atomically_replaces_previous_result_with_readable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bluetooth-sco-repair.json"
            path.write_text("previous result")
            result = {"outcome": "repaired", "originalParameters": [0, 2, 0, 1, 1]}
            sco.write_report(result, path)
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_installer_runs_fixed_root_helper_before_unprivileged_http_on_each_start(self):
        installer = (Path(__file__).resolve().parents[1] / "pi" / "install.sh").read_text()
        unit = installer.split("cat > /etc/systemd/system/denden-demo.service <<EOF\n", 1)[1].split("\nEOF", 1)[0]
        self.assertIn("After=network.target bluetooth.service", unit)
        self.assertIn("ExecStartPre=-+/usr/bin/python3 -E -s -B /opt/denden-demo/bluetooth_sco.py --prestart", unit)
        self.assertIn("User=$DENDEN_USER\n", unit)
        self.assertIn("ExecStart=/usr/bin/python3 /opt/denden-demo/server.py\n", unit)
        self.assertNotIn("ExecStart=+", unit)
        self.assertNotIn("AmbientCapabilities=", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("TimeoutStartSec=75", unit)
        self.assertIn("install -d -o root -g root -m 755 /opt/denden-demo", installer)
        self.assertIn("/usr/bin/python3 -E -s -B install_files.py", installer)
        from install_files import PAYLOAD
        self.assertIn("bluetooth_sco.py", PAYLOAD)
        self.assertLess(installer.index("install_files.py"), installer.index("systemctl restart denden-demo.service"))
        self.assertLess(installer.index("systemctl stop denden-demo.service"), installer.index("install -d -o root"))
        self.assertIn("systemctl enable denden-demo.service\nsystemctl restart denden-demo.service", installer)
        self.assertNotIn("bluetoothctl power", installer)


if __name__ == "__main__":
    unittest.main()
