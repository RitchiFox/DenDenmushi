import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
import servo_pwm
import servo_pwm_setup as setup


class PwmSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sysfs = self.root / "pwm"
        self.sysfs.mkdir()
        self.gpio = self.root / "gpio"
        self.gpio.mkdir()
        (self.gpio / "phandle").write_bytes(struct.pack(">I", 7))
        self.driver = self.root / "drivers" / "pwm-gpio"
        self.driver.mkdir(parents=True)
        self.calls, self.writes, self.ownership = [], [], []
        self.now = 0
        self.fail_command = None
        self.fail_write = None
        self.appear = True

    def chip(self, pin, exported=False):
        chip = self.sysfs / f"pwmchip{pin + 63}"
        (chip / "device" / "of_node").mkdir(parents=True)
        (chip / "npwm").write_text("1\n")
        (chip / "device" / "driver").symlink_to(self.driver)
        node = chip / "device" / "of_node"
        (node / "compatible").write_bytes(b"pwm-gpio\0")
        (node / "gpios").write_bytes(struct.pack(">III", 7, pin, 0))
        (chip / "export").touch()
        if exported:
            self.export(chip)
        return chip

    @staticmethod
    def export(chip):
        pwm = chip / "pwm0"
        pwm.mkdir()
        for name, value in (("enable", "0"), ("period", "0"), ("duty_cycle", "0"), ("polarity", "normal")):
            with (pwm / name).open("w") as handle:
                handle.write(value)

    def runner(self, command, **kwargs):
        self.calls.append(command)
        self.assertEqual(kwargs["timeout"], setup.COMMAND_TIMEOUT)
        self.assertFalse(kwargs["check"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["env"]["LC_ALL"], "C")
        if self.fail_command and self.fail_command(command):
            raise subprocess.TimeoutExpired(command, setup.COMMAND_TIMEOUT)
        if command[0] != str(setup.MODPROBE) and self.appear:
            self.assertEqual(command[:4], ["/usr/bin/dtoverlay", "-d", "/boot/firmware/overlays", "pwm-gpio"])
            self.chip(int(command[4].removeprefix("gpio=")))
        return SimpleNamespace(returncode=0)

    def sleep(self, seconds):
        self.now += seconds

    def prepare(self, **overrides):
        arguments = dict(runner=self.runner, euid=lambda: 0,
                         account_lookup=lambda name: SimpleNamespace(pw_uid=1000, pw_gid=1000),
                         verify_hardware=lambda: None, sysfs_root=self.sysfs, gpio_node=self.gpio,
                         clock=lambda: self.now, sleep=self.sleep,
                         chown=lambda path, uid, gid: self.ownership.append((path, uid, gid)),
                         chmod=lambda path, mode: self.assertEqual(mode, 0o600))
        arguments.update(overrides)
        original_write = Path.write_text
        def write(path, text, *args, **kwargs):
            self.writes.append((path, text))
            if self.fail_write and self.fail_write(path, text):
                raise OSError("injected failure")
            if path.name == "export":
                self.export(path.parent)
            # Model older PWM core: zero-period state cannot be applied.
            if path.name in ("enable", "duty_cycle") and (path.parent / "period").exists():
                if (path.parent / "period").read_text().strip() == "0":
                    raise OSError("zero-period PWM state")
            return original_write(path, text, *args, **kwargs)
        with patch.object(setup, "_trusted_file", return_value=True), patch.object(Path, "write_text", new=write):
            return setup.prepare("owner", **arguments)

    def test_fresh_three_channels_are_loaded_disabled_and_only_two_attributes_granted(self):
        result = self.prepare()
        self.assertTrue(result["ok"])
        self.assertFalse(result["enabled"])
        self.assertEqual([command[-1] for command in self.calls], ["pwm-gpio", "gpio=17", "gpio=27", "gpio=22"])
        channels = servo_pwm.discover(self.sysfs, self.gpio)
        self.assertEqual(set(channels), set(setup.PINS))
        self.assertEqual(len(self.ownership), 6)
        for path, uid, gid in self.ownership:
            self.assertIn(path.name, ("enable", "duty_cycle"))
            self.assertEqual((uid, gid), (1000, 1000))
        for chip in channels.values():
            pwm = chip / "pwm0"
            self.assertEqual((pwm / "enable").read_text().strip(), "0")
            self.assertEqual((pwm / "duty_cycle").read_text().strip(), "0")
            self.assertEqual((pwm / "period").read_text().strip(), "20000000")
            self.assertEqual((pwm / "polarity").read_text().strip(), "normal")
        self.assertFalse(any(path.name == "enable" and value.strip() == "1" for path, value in self.writes))

    def test_repeat_preparation_never_adds_overlays_or_exports_again(self):
        self.prepare()
        self.calls.clear(); self.writes.clear()
        self.prepare()
        self.assertEqual(self.calls, [[str(setup.MODPROBE), "pwm-gpio"]])
        self.assertFalse(any(path.name == "export" for path, _ in self.writes))

    def test_partial_existing_configuration_loads_only_missing_gpio(self):
        existing = self.chip(17)
        self.prepare()
        self.assertEqual([command[-1] for command in self.calls], ["pwm-gpio", "gpio=27", "gpio=22"])
        self.assertEqual(servo_pwm.discover(self.sysfs, self.gpio)[17], existing)

    def test_overlay_failure_disables_prepared_partial_channels(self):
        self.fail_command = lambda command: command[-1] == "gpio=27"
        with self.assertRaisesRegex(setup.SetupError, "pwm_setup_command_failed"):
            self.prepare()
        channels = servo_pwm.discover(self.sysfs, self.gpio)
        self.assertEqual(set(channels), {17})
        self.assertEqual((channels[17] / "pwm0" / "enable").read_text().strip(), "0")
        self.assertEqual((channels[17] / "pwm0" / "duty_cycle").read_text().strip(), "0")

    def test_successful_command_without_verified_chip_is_bounded_failure(self):
        self.appear = False
        with self.assertRaisesRegex(setup.SetupError, "pwm_setup_readiness_timeout"):
            self.prepare()
        self.assertLessEqual(self.now, setup.READINESS_TIMEOUT + 0.01)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.ownership, [])

    def test_wrong_root_owner_or_hardware_never_runs_commands(self):
        for extra, expected in (({"euid": lambda: 1000}, "root_required"),
                                ({"account_lookup": lambda _: SimpleNamespace(pw_uid=0)}, "invalid_owner"),
                                ({"verify_hardware": lambda: (_ for _ in ()).throw(setup.SetupError("hardware_unverified"))}, "hardware_unverified")):
            with self.subTest(expected=expected), self.assertRaisesRegex(setup.SetupError, expected):
                self.prepare(**extra)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.writes, [])

    def test_hardware_guard_rejects_other_model_and_active_tft_without_unbind(self):
        model, compatible, spi = self.root / "model", self.root / "compatible", self.root / "spi"
        model.write_text("Raspberry Pi 3 Model B Rev 1.2\0")
        compatible.write_bytes(b"raspberrypi,3-model-b\0brcm,bcm2837\0")
        setup._verify_hardware(model, compatible, spi)
        model.write_text("Raspberry Pi 4 Model B Rev 1.2\0")
        with self.assertRaisesRegex(setup.SetupError, "unsupported_pi_model"):
            setup._verify_hardware(model, compatible, spi)
        model.write_text("Raspberry Pi 3 Model B Rev 1.2\0")
        driver = self.root / "drivers" / "ads7846"
        driver.mkdir()
        path = spi / "devices" / "spi0.1"
        path.mkdir(parents=True)
        (path / "driver").symlink_to(driver)
        with self.assertRaisesRegex(setup.SetupError, "tft_driver_still_active"):
            setup._verify_hardware(model, compatible, spi)
        self.assertTrue((path / "driver").is_symlink())

    def test_overlay_tool_paths_and_gpio_are_fixed_and_missing_tool_fails(self):
        with self.assertRaisesRegex(setup.SetupError, "unsupported_gpio"):
            setup._overlay_command(18)
        with patch.object(setup, "_trusted_file", side_effect=lambda path, executable=False: not executable):
            with self.assertRaisesRegex(setup.SetupError, "dtoverlay_unavailable"):
                setup._overlay_command(17)

    def test_cli_rejects_arbitrary_operations_and_does_not_expose_unknown_exception(self):
        for argv in ([], ["stop"], ["prepare", "owner", "gpio=18"]):
            output = io.StringIO()
            with patch.object(setup, "prepare", side_effect=AssertionError("must not run")), contextlib.redirect_stdout(output):
                self.assertEqual(setup.main(argv), 1)
            self.assertEqual(json.loads(output.getvalue())["error"], "unsupported_arguments")
        output = io.StringIO()
        with patch.object(setup, "prepare", side_effect=OSError("private details")), contextlib.redirect_stdout(output):
            self.assertEqual(setup.main(["prepare", "owner"]), 1)
        self.assertNotIn("private details", output.getvalue())


if __name__ == "__main__":
    unittest.main()
