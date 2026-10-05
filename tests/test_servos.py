import math
from collections import deque
import io
from pathlib import Path
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
from servos import Servos, ServoClient, LgpioDriver, validate, reject_tft_drivers, _client_command


class Driver:
    def __init__(self): self.calls = []
    def pulse(self, pin, width): self.calls.append((pin, width))
    def extend(self, pin, width): self.calls.append(("extend", pin, width))
    def tick(self): pass


class ServoTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.driver = Driver()
        self.servos = Servos(self.driver, clock=lambda: self.now)

    def test_startup_reads_and_stop_never_emit_a_position(self):
        self.servos.tick()
        self.servos.command({"action": "status"})
        self.servos.command({"action": "stop"})
        self.assertEqual(self.driver.calls, [])
        self.assertTrue(all(not c["active"] and c["angle"] is None for c in self.servos.status()["channels"]))

    def test_exact_wiring_and_conservative_pulses(self):
        for channel, angle in [(1, 45), (2, 90), (3, 135)]:
            self.servos.command({"action": "move", "channel": channel, "angle": angle})
        self.assertEqual(self.driver.calls, [(17, 1250), (27, 1500), (22, 1750)])
        self.assertEqual([c["pin"] for c in self.servos.status()["channels"]], [11, 13, 15])

    def test_first_channel_wide_range_is_opt_in_and_center_is_unchanged(self):
        for angle in (45, 90, 135):
            result = self.servos.command({"action": "move", "channel": 1, "angle": angle, "wide": True})
            self.assertTrue(result["channels"][0]["wide"])
        self.assertEqual(self.driver.calls, [(17, 1000), (17, 1500), (17, 2000)])
        self.assertEqual(result["channels"][0]["pulseWidthMicros"], 2000)
        result = self.servos.command({"action": "move", "channel": 1, "angle": 135})
        self.assertEqual(self.driver.calls[-1], (17, 1750))
        self.assertFalse(result["channels"][0]["wide"])
        self.servos.command({"action": "move", "channel": 2, "angle": 45, "wide": False})
        self.assertEqual(self.driver.calls[-1], (27, 1250))

    def test_wide_move_accepts_lease_and_rejects_noncanonical_requests(self):
        lease = "6e552c68-1943-4e06-8203-caa37ae35573"
        self.servos.command({"action": "move", "channel": 1, "angle": 45, "wide": True, "lease": lease})
        self.assertTrue(self.servos.status()["channels"][0]["holding"])
        self.driver.calls.clear()
        requests = [{"action": "move", "channel": c, "angle": 90, "wide": True} for c in (2, 3)]
        requests += [{"action": "move", "channel": 1, "angle": 90, "wide": value}
                     for value in (None, 1, 0, "true", [], {})]
        requests += [{"action": "move", "channel": 1, "angle": 90, "wide": True, "other": 1},
                     {"action": "release", "channel": 1, "wide": False},
                     {"action": "status", "wide": True}]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.servos.command(request)
        self.assertEqual(self.driver.calls, [])

    def test_each_move_expires_without_any_client_or_heartbeat(self):
        self.servos.command({"action": "move", "channel": 1, "angle": 90})
        self.now += 2
        self.servos.command({"action": "move", "channel": 2, "angle": 90})
        self.now += 1.01
        self.servos.tick()
        self.assertEqual(self.servos.active, [False, True, False])
        self.now += 2
        self.servos.tick()
        self.assertEqual(self.servos.active, [False] * 3)
        self.assertEqual(self.driver.calls[-2:], [(17, 0), (27, 0)])

    def test_reading_status_does_not_keep_servo_energized(self):
        self.servos.command({"action": "move", "channel": 3, "angle": 90})
        for _ in range(40):
            self.now += .1
            self.servos.command({"action": "status"})
        self.assertEqual(self.driver.calls, [(22, 1500), (22, 0)])

    def test_release_one_and_stop_all_are_idempotent(self):
        for channel in (1, 2, 3):
            self.servos.command({"action": "move", "channel": channel, "angle": 90})
        self.servos.command({"action": "release", "channel": 2})
        self.assertEqual(self.servos.active, [True, False, True])
        self.servos.command({"action": "stop"})
        before = list(self.driver.calls)
        self.servos.command({"action": "stop"})
        self.assertEqual(self.driver.calls, before)
        self.assertEqual(self.servos.active, [False] * 3)

    def test_invalid_requests_cannot_touch_gpio(self):
        requests = [None, [], {}, {"action": "shell"}, {"action": "status", "gpio": 1},
                    {"action": "stop", "channel": 1}, {"action": "move", "channel": 1},
                    {"action": "release", "channel": 1, "angle": 90}]
        requests += [{"action": "move", "channel": c, "angle": 90} for c in [True, False, 0, 4, 17, 1.0, "1", None]]
        requests += [{"action": "move", "channel": 1, "angle": a} for a in [True, False, -90, 0, 44.9, 135.1, 180, 10**350, "90", None, math.nan, math.inf, -math.inf]]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.servos.command(request)
        self.assertEqual(self.driver.calls, [])

    def test_stop_attempts_other_channels_after_hardware_failure(self):
        for channel in (1, 2, 3):
            self.servos.command({"action": "move", "channel": channel, "angle": 90})
        def pulse(pin, width):
            if pin == 17: raise OSError("failed")
            self.driver.calls.append((pin, width))
        self.driver.pulse = pulse
        with self.assertRaises(OSError): self.servos.stop()
        self.assertEqual(self.servos.active, [True, False, False])

    def test_missing_broker_is_an_error_not_simulated_success(self):
        result = ServoClient("/nonexistent/denden-test.sock").command({"action": "status"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "servo_service_unavailable")

    def test_live_tft_drivers_are_rejected_without_unbinding(self):
        for device_name, driver_name in [("spi0.0", "fb_ili9486"), ("spi0.1", "ads7846")]:
            with self.subTest(driver=driver_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                device = root / "devices" / device_name
                device.mkdir(parents=True)
                driver = root / "drivers" / driver_name
                driver.mkdir(parents=True)
                (driver / "unbind").write_text("")
                (device / "driver").symlink_to(driver)
                with self.assertRaisesRegex(OSError, "tft_driver_still_active"):
                    reject_tft_drivers(root)
                self.assertEqual((driver / "unbind").read_text(), "")

    def test_missing_tft_does_not_require_sysfs_mutations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reject_tft_drivers(root)
            self.assertEqual(list(root.iterdir()), [])

    def test_hold_requires_matching_keepalive_and_expires_without_it(self):
        lease = "6e552c68-1943-4e06-8203-caa37ae35573"
        self.servos.command({"action": "move", "channel": 1, "angle": 90, "lease": lease})
        self.assertTrue(self.servos.status()["channels"][0]["holding"])
        self.now = 102
        self.servos.command({"action": "keepalive", "lease": lease})
        self.assertEqual(self.driver.calls[-1], ("extend", 17, 1500))
        self.now = 105
        self.servos.command({"action": "keepalive", "lease": "7e552c68-1943-4e06-8203-caa37ae35573"})
        self.servos.command({"action": "status"})
        self.now = 106
        self.servos.tick()
        self.assertFalse(self.servos.active[0])
        count = len(self.driver.calls)
        self.servos.command({"action": "keepalive", "lease": lease})
        self.assertEqual(len(self.driver.calls), count)  # Never revive expired motion.

    def test_stop_cancels_hold_so_late_keepalive_cannot_restart_it(self):
        lease = "6e552c68-1943-4e06-8203-caa37ae35573"
        self.servos.command({"action": "move", "channel": 2, "angle": 90, "lease": lease})
        self.servos.command({"action": "stop"})
        count = len(self.driver.calls)
        self.servos.command({"action": "keepalive", "lease": lease})
        self.assertEqual(len(self.driver.calls), count)
        self.assertFalse(any(self.servos.active))

    def test_invalid_hold_requests_do_not_write(self):
        for lease in (None, True, "", "x" * 36, "6E552C68-1943-4E06-8203-CAA37AE35573"):
            for body in ({"action": "keepalive", "lease": lease},
                         {"action": "move", "channel": 1, "angle": 90, "lease": lease}):
                with self.assertRaises(ValueError): self.servos.command(body)
        self.assertEqual(self.driver.calls, [])

    def test_installer_enables_autostart_only_after_hardware_check(self):
        installer = (Path(__file__).parents[1] / "pi/install.sh").read_text()
        disable = installer.index("systemctl disable --now denden-motion.service")
        start = installer.index("systemctl restart denden-motion.service")
        check = installer.index("Three servo controls verified; no movement requested.")
        enable = installer.index("systemctl enable denden-motion.service")
        self.assertLess(disable, start)
        self.assertLess(start, check)
        self.assertLess(check, enable)
        self.assertLess(installer.index("systemctl restart denden-demo.service"), start)
        self.assertLess(installer.index("systemctl restart denden-setup.service"), start)
        self.assertNotIn("systemctl restart denden-servos.service", installer)
        unit = installer.split("cat > /etc/systemd/system/denden-demo.service <<EOF\n", 1)[1].split("\nEOF", 1)[0]
        wants = [line for line in unit.splitlines() if line.startswith("Wants=")][0]
        self.assertNotIn("denden-servos.service", wants)


class FakeLgpio:
    class error(Exception): pass
    BAD_PWM_MICROS = -86
    TX_PWM = 0
    @staticmethod
    def error_text(code): return "bad PWM micros" if code == -86 else "other failure"
    def __init__(self):
        self.calls = []
        self.fail_pin = None
        self.info = [0, 54, "gpiochip0", "pinctrl-bcm2835"]
        self.queues = {pin: deque() for pin in (17, 27, 22)}
        self.emitted = {pin: [] for pin in self.queues}
    def gpiochip_open(self, chip):
        self.calls.append(("open", chip))
        return 42
    def gpio_get_chip_info(self, handle): return self.info
    def gpio_claim_output(self, handle, pin, level):
        self.calls.append(("claim", handle, pin, level))
        if pin == self.fail_pin: raise self.error("GPIO busy")
        return 0
    def tx_room(self, handle, pin, kind): return 8 - len(self.queues[pin])
    def tx_pulse(self, *args):
        self.calls.append(("pulse", *args)); self.queues[args[1]].clear(); return 0
    def tx_servo(self, *args):
        self.calls.append(("servo", *args))
        _, pin, width, frequency, offset, cycles = args
        assert frequency == 50 and offset == 0 and cycles > 0
        assert len(self.queues[pin]) < 8
        self.queues[pin].append([width, cycles])
        return 8 - len(self.queues[pin])
    def gpio_write(self, *args): self.calls.append(("write", *args)); return 0
    def gpiochip_close(self, handle):
        self.calls.append(("close", handle))
        for queue in self.queues.values(): queue.clear()
        return 0

    def consume(self, pin, cycles=2):
        """Consume complete 20 ms periods without inventing new output."""
        for _ in range(cycles):
            if not self.queues[pin]: break
            group = self.queues[pin][0]
            self.emitted[pin].append(group[0])
            group[1] -= 1
            if group[1] == 0: self.queues[pin].popleft()

    def remaining_seconds(self, pin):
        return sum(group[1] for group in self.queues[pin]) / 50


class LgpioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model = self.root / "model"
        self.model.write_text("Raspberry Pi 3 Model B Rev 1.2\0")
        self.lib = FakeLgpio()

    def driver(self):
        return LgpioDriver(self.lib, self.model, self.root / "spi")

    def test_claims_low_without_starting_pulses(self):
        driver = self.driver()
        self.assertEqual(self.lib.calls, [("open", 0)] + [("claim", 42, pin, 0) for pin in (17, 27, 22)])
        driver.close()

    def test_conflict_releases_previous_claims_without_movement(self):
        self.lib.fail_pin = 27
        with self.assertRaises(OSError): self.driver()
        self.assertEqual(self.lib.calls[-1], ("close", 42))
        self.assertFalse(any(c[0] == "servo" for c in self.lib.calls))
        self.assertNotIn(("claim", 42, 22, 0), self.lib.calls)

    def test_unknown_chip_is_rejected_before_claims(self):
        self.lib.info[3] = "unknown"
        with self.assertRaisesRegex(OSError, "unexpected_gpiochip"): self.driver()
        self.assertEqual(self.lib.calls, [("open", 0), ("close", 42)])

    def test_first_move_queues_at_most_120ms_and_release_clears_it(self):
        driver = self.driver()
        self.lib.calls.clear()
        driver.pulse(27, 1500)
        self.assertEqual(self.lib.calls, [("servo", 42, 27, 1500, 50, 0, 2)] * 3)
        self.assertAlmostEqual(self.lib.remaining_seconds(27), .12)
        self.lib.calls.clear()
        driver.pulse(27, 0)
        self.assertEqual(self.lib.calls, [("pulse", 42, 27, 0, 0), ("write", 42, 27, 0)])
        self.assertEqual(self.lib.remaining_seconds(27), 0)
        self.lib.calls.clear()
        driver.extend(27, 1500)
        driver.tick()
        self.assertEqual(self.lib.calls, [])
        driver.close()

    def test_driver_rejects_unlisted_pins_and_out_of_range_pulses(self):
        driver = self.driver()
        self.lib.calls.clear()
        for pin, width in [(18, 1500), (17, -1), (22, 2001), (27, 999), (27, True), (17.0, 1500)]:
            with self.assertRaises(ValueError): driver.pulse(pin, width)
        self.assertEqual(self.lib.calls, [])
        driver.close()

    def test_idle_stop_error_does_not_block_release(self):
        driver = self.driver()
        def idle_stop(*args):
            raise self.lib.error(self.lib.error_text(self.lib.BAD_PWM_MICROS))
        self.lib.tx_pulse = idle_stop
        driver.pulse(17, 1500)
        self.assertIn(("servo", 42, 17, 1500, 50, 0, 2), self.lib.calls)
        driver.pulse(17, 0)
        driver.close()

    def test_other_stop_errors_are_not_ignored(self):
        driver = self.driver()
        def failed_stop(*args): raise self.lib.error("GPIO not allocated")
        self.lib.tx_pulse = failed_stop
        driver.pulse(17, 1500)
        self.lib.calls.clear()
        with self.assertRaises(OSError): driver.pulse(17, 0)
        driver.tick()
        self.assertFalse(any(c[0] == "servo" for c in self.lib.calls))
        driver.close()
        self.assertEqual(self.lib.calls[-1], ("close", 42))
        self.lib.calls.clear()
        driver.close()
        self.assertEqual(self.lib.calls, [])

    def test_retarget_preserves_submitted_pulses_and_slews_at_complete_periods(self):
        driver = self.driver()
        driver.pulse(17, 1500)
        original = [list(group) for group in self.lib.queues[17]]
        self.lib.calls.clear()
        driver.pulse(17, 1750)
        self.assertEqual(self.lib.calls, [])  # No truncated pulse or phase reset.
        self.assertEqual(list(self.lib.queues[17]), original)
        for _ in range(35):
            self.lib.consume(17)
            driver.tick()
            self.assertLessEqual(self.lib.remaining_seconds(17), .12)
        widths = self.lib.emitted[17]
        self.assertTrue(all(0 <= b-a <= 10 for a,b in zip(widths, widths[1:])))
        self.assertEqual(widths[-1], 1750)
        self.assertTrue(all(c[0] == "servo" and c[-1] == 2 for c in self.lib.calls))
        driver.pulse(17, 1250)
        for _ in range(60):
            self.lib.consume(17)
            driver.tick()
        self.assertEqual(self.lib.emitted[17][-1], 1250)
        self.assertTrue(all(abs(b-a) <= 10 for a,b in zip(self.lib.emitted[17], self.lib.emitted[17][1:])))
        driver.pulse(17, 0)
        self.lib.calls.clear()
        driver.tick()
        self.assertEqual(self.lib.calls, [])
        driver.close()

    def test_frequent_retargets_coalesce_without_restarting_or_jumping(self):
        driver = self.driver()
        driver.pulse(17, 1500)
        self.lib.calls.clear()
        for target in (1510, 1800, 1000, 2000, 1250): driver.pulse(17, target)
        self.assertEqual(self.lib.calls, [])
        self.lib.consume(17)
        driver.tick()
        self.assertEqual(self.lib.calls, [("servo", 42, 17, 1490, 50, 0, 2)])
        driver.extend(17, 2000)  # Stale target cannot replace the latest one.
        self.assertEqual(driver.targets[17], 1250)
        self.assertLessEqual(self.lib.remaining_seconds(17), .12)
        driver.close()

    def test_continuous_hold_stays_bounded_and_expires_after_last_valid_lease(self):
        now = [0.0]
        driver = self.driver()
        servos = Servos(driver, clock=lambda: now[0])
        lease = "6e552c68-1943-4e06-8203-caa37ae35573"
        servos.command({"action": "move", "channel": 1, "angle": 135, "wide": True, "lease": lease})
        self.lib.calls.clear()
        for step in range(1, 351):
            now[0] = step * .04
            self.lib.consume(17)
            if step % 50 == 0:
                servos.command({"action": "keepalive", "lease": lease})
            else:
                servos.tick()
            self.assertLessEqual(self.lib.remaining_seconds(17), .12)
        self.assertTrue(all(c[0] == "servo" and c[-1] == 2 for c in self.lib.calls))
        self.assertEqual(set(self.lib.emitted[17]), {2000})
        now[0] = 17.99
        servos.command({"action": "keepalive", "lease": "7e552c68-1943-4e06-8203-caa37ae35573"})
        now[0] = 18.0
        servos.tick()
        self.assertFalse(servos.active[0])
        self.assertEqual(self.lib.remaining_seconds(17), 0)
        self.lib.calls.clear()
        servos.command({"action": "keepalive", "lease": lease})
        driver.extend(17, 2000)
        driver.tick()
        self.assertEqual(self.lib.calls, [])
        driver.close()

    def test_close_discards_intent_and_all_finite_groups(self):
        driver = self.driver()
        for pin, width in ((17, 1000), (27, 1500), (22, 2000)): driver.pulse(pin, width)
        driver.close()
        self.lib.calls.clear()
        driver.tick()
        for pin in (17, 27, 22): driver.extend(pin, 1500)
        self.assertEqual(self.lib.calls, [])
        self.assertTrue(all(not queue for queue in self.lib.queues.values()))


class SocketTests(unittest.TestCase):
    class Client:
        def __init__(self, raw): self.raw = raw
        def makefile(self, mode):
            if isinstance(self.raw, Exception): raise self.raw
            return io.BytesIO(self.raw)

    def test_socket_read_timeout_or_disconnect_does_not_become_hardware_failure(self):
        for error in (socket.timeout("timed out"), ConnectionResetError("disconnected")):
            with self.subTest(error=error):
                driver = Driver()
                result = _client_command(self.Client(error), Servos(driver))
                self.assertFalse(result["ok"])
                self.assertEqual(driver.calls, [])

    def test_hardware_failure_propagates_out_of_client_handler(self):
        driver = Driver()
        def fail(pin, width): raise OSError("hardware failure")
        driver.pulse = fail
        with self.assertRaisesRegex(OSError, "hardware failure"):
            _client_command(self.Client(b'{"action":"move","channel":1,"angle":90}\n'), Servos(driver))

    def test_invalid_socket_frames_never_touch_outputs(self):
        for raw in (b'{"action":"stop"}', b'{}\n', b'x' * 512 + b'\n', b'garbage\n'):
            with self.subTest(raw=raw):
                driver = Driver()
                result = _client_command(self.Client(raw), Servos(driver))
                self.assertFalse(result["ok"])
                self.assertEqual(driver.calls, [])


if __name__ == "__main__": unittest.main()
