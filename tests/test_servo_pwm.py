from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
import servo_pwm
from servo_pwm import KernelPwmDriver, discover, stop_all


class KernelPwmTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sysfs = self.root / "pwm"
        self.sysfs.mkdir()
        self.gpio_node = self.root / "gpio-controller"
        self.gpio_node.mkdir()
        (self.gpio_node / "phandle").write_bytes(struct.pack(">I", 0x123))
        self.driver_dir = self.root / "drivers" / "pwm-gpio"
        self.driver_dir.mkdir(parents=True)

    def chip(self, number, pin, controller=0x123, flags=0, driver="pwm-gpio", exported=True):
        chip = self.sysfs / f"pwmchip{number}"
        (chip / "device" / "of_node").mkdir(parents=True)
        (chip / "npwm").write_text("1\n")
        driver_path = self.root / "drivers" / driver
        driver_path.mkdir(exist_ok=True)
        (chip / "device" / "driver").symlink_to(driver_path)
        node = chip / "device" / "of_node"
        (node / "compatible").write_bytes(b"pwm-gpio\0")
        (node / "gpios").write_bytes(struct.pack(">III", controller, pin, flags))
        if exported:
            (chip / "pwm0").mkdir()
            for name, value in (("enable", "0"), ("period", "20000000"),
                                ("duty_cycle", "0"), ("polarity", "normal")):
                (chip / "pwm0" / name).write_text(value + "\n")
        return chip

    def prepared(self):
        return {pin: self.chip(number, pin) for pin, number in ((17, 41), (27, 3), (22, 106))}

    def driver(self):
        return KernelPwmDriver(self.sysfs, self.gpio_node)

    def writes(self, failure=None):
        calls = []
        def write(path, value):
            calls.append((path, value))
            if failure and failure(path, value):
                raise OSError("injected write failure")
            path.write_text(f"{value}\n")
        return calls, patch.object(servo_pwm, "_write", side_effect=write)

    def test_reordered_chip_ids_are_mapped_by_identity_with_no_startup_writes(self):
        chips = self.prepared()
        calls, writer = self.writes()
        with writer:
            driver = self.driver()
            driver.tick()
            driver.extend(17, 1500)
        self.assertEqual(driver.channels, chips)
        self.assertEqual(discover(self.sysfs, self.gpio_node), chips)
        self.assertEqual(calls, [])

    def test_first_enable_sets_duty_before_start_and_retarget_never_disables(self):
        chips = self.prepared()
        driver = self.driver()
        pwm = chips[17] / "pwm0"
        calls, writer = self.writes()
        with writer:
            driver.pulse(17, 1500)
            self.assertEqual(calls, [(pwm / "duty_cycle", 1500000), (pwm / "enable", 1)])
            calls.clear()
            driver.pulse(17, 1528)
            driver.extend(17, 1528)
            driver.extend(17, 1500)
            driver.tick()
        self.assertEqual(calls, [(pwm / "duty_cycle", 1528000)])
        self.assertEqual(driver.targets, {17: 1528})

    def test_release_disables_then_zeros_and_late_keepalive_never_revives(self):
        chips = self.prepared()
        driver = self.driver()
        driver.pulse(27, 2000)
        calls, writer = self.writes()
        with writer:
            driver.pulse(27, 0)
            driver.extend(27, 2000)
            driver.tick()
        pwm = chips[27] / "pwm0"
        self.assertEqual(calls, [(pwm / "enable", 0), (pwm / "duty_cycle", 0)])
        self.assertNotIn(27, driver.targets)
        self.assertNotIn(27, driver.enabled)

    def test_validation_never_touches_outputs(self):
        self.prepared()
        driver = self.driver()
        calls, writer = self.writes()
        with writer:
            for pin, width in ((18, 1500), (True, 1500), (17.0, 1500), (17, True),
                               (17, 1500.0), (17, 499), (27, 2501), (22, -1)):
                with self.subTest(pin=pin, width=width), self.assertRaises(ValueError):
                    driver.pulse(pin, width)
            with self.assertRaises(ValueError): driver.extend(17, 0)
        self.assertEqual(calls, [])
        driver.pulse(17, 1000)
        driver.pulse(17, 2000)
        self.assertEqual(driver.targets[17], 2000)

    def test_foreign_controller_driver_flags_and_gpio_are_never_written(self):
        chips = self.prepared()
        foreign = [self.chip(1, 17, controller=0x124),
                   self.chip(2, 17, driver="other-pwm"),
                   self.chip(4, 17, flags=1), self.chip(5, 18)]
        bad_compatible = self.chip(6, 17)
        (bad_compatible / "device" / "of_node" / "compatible").write_bytes(b"other-pwm\0")
        foreign.append(bad_compatible)
        bad_npwm = self.chip(7, 17)
        (bad_npwm / "npwm").write_text("2\n")
        foreign.append(bad_npwm)
        bad_cells = self.chip(8, 17)
        (bad_cells / "device" / "of_node" / "gpios").write_bytes(struct.pack("<III", 0x123, 17, 0))
        foreign.append(bad_cells)
        for chip in foreign:
            (chip / "pwm0" / "enable").write_text("1\n")
            (chip / "pwm0" / "duty_cycle").write_text("1500000\n")
        self.assertEqual(discover(self.sysfs, self.gpio_node), chips)
        self.driver().close()
        stop_all(self.sysfs, self.gpio_node)
        for chip in foreign:
            self.assertEqual((chip / "pwm0" / "enable").read_text(), "1\n")
            self.assertEqual((chip / "pwm0" / "duty_cycle").read_text(), "1500000\n")

    def test_foreign_identity_cannot_satisfy_required_channel(self):
        known = [self.chip(1, 17), self.chip(2, 27)]
        for chip in known: (chip / "pwm0" / "enable").write_text("1\n")
        foreign = self.chip(3, 22, controller=0x456)
        calls, writer = self.writes()
        with writer, self.assertRaisesRegex(OSError, "channels_not_prepared"):
            self.driver()
        self.assertEqual(set(path.parent.parent for path, _ in calls), set(known))
        self.assertFalse(any(foreign in path.parents for path, _ in calls))

    def test_invalid_startup_state_cleans_all_known_channels_without_enabling(self):
        chips = self.prepared()
        for attribute, invalid in (("enable", "1"), ("period", "10000000"),
                                   ("duty_cycle", "1500000"), ("polarity", "inversed")):
            with self.subTest(attribute=attribute):
                for chip in chips.values():
                    for name, value in (("enable", "0"), ("period", "20000000"),
                                        ("duty_cycle", "0"), ("polarity", "normal")):
                        (chip / "pwm0" / name).write_text(value + "\n")
                (chips[22] / "pwm0" / attribute).write_text(invalid + "\n")
                calls, writer = self.writes()
                with writer, self.assertRaisesRegex(OSError, "not_idle"):
                    self.driver()
                self.assertTrue(set(path.parent.parent for path, _ in calls) <= set(chips.values()))
                self.assertTrue(all(value == 0 for _, value in calls))
                for chip in chips.values():
                    self.assertEqual((chip / "pwm0" / "enable").read_text(), "0\n")
                    self.assertEqual((chip / "pwm0" / "duty_cycle").read_text(), "0\n")

    def test_missing_export_causes_cleanup_only_of_prepared_known_channels(self):
        exported = self.chip(1, 17)
        (exported / "pwm0" / "enable").write_text("1\n")
        (exported / "pwm0" / "duty_cycle").write_text("1500000\n")
        self.chip(2, 27, exported=False)
        self.chip(3, 22, exported=False)
        calls, writer = self.writes()
        with writer, self.assertRaises(OSError): self.driver()
        self.assertEqual(calls, [(exported / "pwm0" / "enable", 0),
                                 (exported / "pwm0" / "duty_cycle", 0)])

    def test_close_attempts_every_channel_after_failure_and_blocks_restart(self):
        chips = self.prepared()
        driver = self.driver()
        for pin in (17, 27, 22): driver.pulse(pin, 1500)
        calls, writer = self.writes(failure=lambda path, value: path == chips[17] / "pwm0" / "enable")
        with writer, self.assertRaisesRegex(OSError, "stop_failed"):
            driver.close()
        self.assertEqual(len(calls), 6)
        for pin in (27, 22): self.assertEqual((chips[pin] / "pwm0" / "enable").read_text(), "0\n")
        self.assertEqual(driver.targets, {})
        with self.assertRaisesRegex(OSError, "closed"): driver.pulse(17, 1500)
        calls, writer = self.writes()
        with writer:
            driver.extend(17, 1500)
            driver.tick()
        self.assertEqual(calls, [])

    def test_release_attempts_duty_zero_even_if_disable_fails(self):
        chips = self.prepared()
        driver = self.driver()
        driver.pulse(17, 1500)
        pwm = chips[17] / "pwm0"
        calls, writer = self.writes(failure=lambda path, value: path == pwm / "enable")
        with writer, self.assertRaises(OSError): driver.pulse(17, 0)
        self.assertEqual(calls, [(pwm / "enable", 0), (pwm / "duty_cycle", 0)])
        self.assertEqual(driver.targets, {})

    def test_independent_stop_handles_partial_unexported_and_duplicate_matches(self):
        first = self.chip(1, 17)
        duplicate = self.chip(2, 17)
        self.chip(3, 27, exported=False)
        for chip in (first, duplicate):
            (chip / "pwm0" / "enable").write_text("1\n")
        with self.assertRaisesRegex(OSError, "duplicate_pwm_gpio"):
            discover(self.sysfs, self.gpio_node)
        stop_all(self.sysfs, self.gpio_node)
        for chip in (first, duplicate):
            self.assertEqual((chip / "pwm0" / "enable").read_text(), "0\n")

    def test_missing_controller_identity_prevents_all_writes(self):
        self.prepared()
        (self.gpio_node / "phandle").write_bytes(b"bad")
        calls, writer = self.writes()
        with writer:
            with self.assertRaisesRegex(OSError, "phandle"): self.driver()
            with self.assertRaisesRegex(OSError, "phandle"): stop_all(self.sysfs, self.gpio_node)
        self.assertEqual(calls, [])

    def test_stop_skips_fresh_zero_period_export_without_writes(self):
        chip = self.chip(1, 17)
        (chip / "pwm0" / "period").write_text("0\n")
        calls, writer = self.writes(failure=lambda path, value: True)
        with writer: stop_all(self.sysfs, self.gpio_node)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
