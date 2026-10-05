import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
import bluetooth_diagnostics as diagnostic


ADAPTER = """hci0:   Type: Primary  Bus: UART
        BD Address: B8:27:EB:93:81:C2  ACL MTU: 1021:8  SCO MTU: 64:1
        UP RUNNING PSCAN
"""
RESPONSE = """< HCI Command: ogf 0x3f, ocf 0x001d, plen 0
> HCI Event: 0x0e plen 9
  01 1D FC 00 01 02 00 01 01
"""


class BluetoothDiagnosticTests(unittest.TestCase):
    def probe(self, adapter=ADAPTER, response=RESPONSE, hardware=None):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            value = adapter if tuple(command) == diagnostic.ADAPTER_COMMAND else response
            if isinstance(value, Exception):
                raise value
            if not isinstance(value, str):
                return value
            return SimpleNamespace(returncode=0, stdout=value, stderr="")
        def read_bytes(path):
            if hardware is not None:
                return hardware
            return (b"raspberrypi,3-model-b\0brcm,bcm2837\0" if path == diagnostic.MODEL_COMPATIBLE
                    else b"brcm,bcm43438-bt\0")
        return diagnostic.probe(runner=runner, read_bytes=read_bytes), calls

    def test_read_success_issues_only_two_fixed_read_commands_with_timeout(self):
        result, calls = self.probe()
        self.assertEqual(result["outcome"], "read")
        self.assertEqual(result["parameters"], [1, 2, 0, 1, 1])
        self.assertEqual(result["routing"], "hci_transport")
        self.assertEqual(result["adapter"]["address"], "B8:27:EB:93:81:C2")
        self.assertEqual([tuple(command) for command, _ in calls],
                         [diagnostic.ADAPTER_COMMAND, diagnostic.READ_COMMAND])
        for _, kwargs in calls:
            self.assertEqual(kwargs["timeout"], 5)
            self.assertNotIn("shell", kwargs)

    def test_pcm_is_distinguished_from_verified_unknown_routing(self):
        for byte, name in (("00", "pcm"), ("02", "codec"), ("03", "i2s"), ("FF", "unknown")):
            with self.subTest(byte=byte):
                result, _ = self.probe(response=RESPONSE.replace("FC 00 01", "FC 00 " + byte))
                self.assertEqual(result["outcome"], "read")
                self.assertEqual(result["routing"], name)

    def test_unverified_hardware_never_opens_controller(self):
        result, calls = self.probe(hardware=b"brcm,bcm43438-bt\0")
        self.assertEqual(result["error"], "hardware_unverified")
        self.assertEqual(calls, [])
        with patch.object(diagnostic, "Path") as path:
            path.return_value.read_bytes.side_effect = FileNotFoundError("private path")
            result = diagnostic.probe(runner=lambda *a, **k: self.fail("command executed"))
        self.assertEqual(result["error"], "hardware_unverified")
        self.assertNotIn("private", json.dumps(result))

    def test_usb_invalid_address_or_extra_adapter_never_sends_vendor_command(self):
        for output in (ADAPTER.replace("UART", "USB"), ADAPTER.replace("hci0:", "hci1:"),
                       ADAPTER.replace("B8:27:EB:93:81:C2", "00:00:00:00:00:00"),
                       ADAPTER.replace("B8:27:EB:93:81:C2", "invalid"),
                       ADAPTER + ADAPTER.replace("hci0:", "hci1:")):
            with self.subTest(output=output):
                result, calls = self.probe(adapter=output)
                self.assertEqual(result["error"], "adapter_unverified")
                self.assertEqual(len(calls), 1)

    def test_unrelated_or_malformed_event_is_inconclusive(self):
        for output in (RESPONSE.replace("1D FC", "1C FC"), RESPONSE.replace("plen 9", "plen 8"),
                       RESPONSE.replace("0x0e", "0x0f"), RESPONSE + "  00\n",
                       RESPONSE + RESPONSE, RESPONSE.replace("  01 1D", "  1D"), ""):
            with self.subTest(output=output):
                result, _ = self.probe(response=output)
                self.assertEqual(result["outcome"], "inconclusive")
                self.assertEqual(result["error"], "response_unverified")
                self.assertNotIn("parameters", result)
                self.assertNotIn("routing", result)

    def test_rejected_command_never_interprets_parameters(self):
        result, _ = self.probe(response=RESPONSE.replace("1D FC 00", "1D FC 01"))
        self.assertEqual(result["error"], "controller_read_rejected")
        self.assertNotIn("parameters", result)

    def test_command_failure_and_timeout_are_nonfatal_and_do_not_expose_output(self):
        failures = [
            (subprocess.TimeoutExpired("private command", 5, output="private output"), "command_timeout"),
            (PermissionError("private path"), "permission_denied"),
            (FileNotFoundError("private path"), "command_unavailable"),
            (SimpleNamespace(returncode=1, stdout="private stdout", stderr="Operation not permitted: private"), "permission_denied"),
            (SimpleNamespace(returncode=1, stdout="private stdout", stderr="private stderr"), "command_failed"),
        ]
        for failure, code in failures:
            with self.subTest(code=code):
                result, _ = self.probe(response=failure)
                self.assertEqual(result["outcome"], "inconclusive")
                self.assertEqual(result["error"], code)
                self.assertNotIn("private", json.dumps(result))

    def test_cli_errors_return_zero_without_accepting_device_or_command_arguments(self):
        output = io.StringIO()
        with patch.object(diagnostic, "probe", side_effect=AssertionError("must not probe")):
            with contextlib.redirect_stdout(output):
                self.assertEqual(diagnostic.main(["hci1", "0x001c"]), 0)
        self.assertEqual(json.loads(output.getvalue())["error"], "unsupported_arguments")
        with patch.object(diagnostic, "probe", return_value={"outcome": "inconclusive"}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(diagnostic.main([]), 0)


if __name__ == "__main__":
    unittest.main()
