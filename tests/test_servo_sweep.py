"""Autonomous first-servo trajectories: bounded motion and lease fail-safes."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
from servos import Servos


LEASE = "6e552c68-1943-4e06-8203-caa37ae35573"
FOREIGN_LEASE = "7e552c68-1943-4e06-8203-caa37ae35573"


class FakeDriver:
    name = "kernel-pwm"

    def __init__(self):
        self.calls = []
        self.ticks = 0

    def pulse(self, pin, width):
        self.calls.append(("pulse", pin, width))

    def extend(self, pin, width):
        self.calls.append(("extend", pin, width))

    def tick(self):
        self.ticks += 1


class ServoSweepTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.driver = FakeDriver()
        self.servos = Servos(self.driver, clock=lambda: self.now)
        self.remember_position(90)

    def remember_position(self, angle, wide=False):
        self.servos.command({"action": "move", "channel": 1,
                             "angle": angle, "wide": wide})
        self.servos.command({"action": "release", "channel": 1})
        self.driver.calls.clear()

    def request(self, **changes):
        body = {"action": "sweep", "channel": 1, "startAngle": 45,
                "endAngle": 90, "durationSeconds": 5, "wide": True,
                "lease": LEASE}
        body.update(changes)
        return body

    def channel(self):
        return self.servos.status()["channels"][0]

    def pulses(self):
        return [(pin, width) for action, pin, width in self.driver.calls
                if action == "pulse"]

    def finish_motion(self, limit=20):
        """Only tick locally; the remote client sends lease renewals, not angles."""
        start = self.now
        next_keepalive = self.now + 1
        states = []
        while self.channel()["moving"] and self.now - start < limit:
            self.now += .020001
            self.servos.tick()
            if self.now >= next_keepalive:
                self.servos.command({"action": "keepalive", "lease": LEASE})
                next_keepalive = self.now + 1
            states.append(self.channel().copy())
        self.assertFalse(self.channel()["moving"], "trajectory did not finish")
        return states

    def test_startup_and_status_do_not_schedule_motion(self):
        fresh = Servos(FakeDriver(), clock=lambda: self.now)
        for state in fresh.command({"action": "status"})["channels"]:
            self.assertFalse(state["moving"])
            self.assertFalse(state["preparing"])
            self.assertFalse(state["active"])
        self.assertEqual(fresh.driver.calls, [])

    def test_strict_schema_and_numeric_limits_reject_without_gpio(self):
        invalid = []
        for key in self.request():
            body = self.request()
            del body[key]
            invalid.append(body)
        invalid.extend([self.request(extra=True), self.request(angle=45)])
        for value in (True, False, 0, 2, 3, 17, 1.0, "1", None):
            invalid.append(self.request(channel=value))
        for key in ("startAngle", "endAngle"):
            for value in (True, False, 44.9, 135.1, 10 ** 350, None,
                          "90", math.nan, math.inf, -math.inf):
                invalid.append(self.request(**{key: value}))
        for value in (True, False, 1.999, 15.001, 10 ** 350, None,
                      "5", math.nan, math.inf, -math.inf):
            invalid.append(self.request(durationSeconds=value))
        for value in (None, 0, 1, "true", [], {}):
            invalid.append(self.request(wide=value))
        for value in (None, True, "", "x" * 36, LEASE.upper()):
            invalid.append(self.request(lease=value))
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.servos.command(body)
        self.assertEqual(self.driver.calls, [])

    def test_supported_numeric_boundaries_can_be_scheduled(self):
        for start, end, duration, wide in ((45, 135, 2, False),
                                           (135.0, 45.0, 15.0, True)):
            with self.subTest(start=start, end=end, duration=duration):
                self.remember_position(90)
                state = self.servos.command(self.request(
                    startAngle=start, endAngle=end,
                    durationSeconds=duration, wide=wide))["channels"][0]
                self.assertTrue(state["moving"])
                self.servos.command({"action": "stop"})

    def test_unknown_starting_pulse_is_rejected_without_output(self):
        driver = FakeDriver()
        fresh = Servos(driver, clock=lambda: self.now)
        with self.assertRaises(ValueError):
            fresh.command(self.request())
        self.assertEqual(driver.calls, [])

    def test_starting_pulse_outside_selected_range_is_rejected(self):
        self.remember_position(45, wide=True)  # 1000 us is outside normal mode.
        with self.assertRaises(ValueError):
            self.servos.command(self.request(wide=False))
        self.assertEqual(self.driver.calls, [])

    def test_sweep_requires_the_kernel_driver(self):
        self.driver.name = "lgpio"
        with self.assertRaises(ValueError):
            self.servos.command(self.request())
        self.assertEqual(self.driver.calls, [])

    def test_range_change_reuses_last_pulse_without_an_initial_jump(self):
        self.remember_position(135, wide=False)  # 1750 us, not wide-mode 2000 us.
        state = self.servos.command(self.request())["channels"][0]
        self.assertEqual(self.pulses(), [(17, 1750)])
        self.assertEqual(state["pulseWidthMicros"], 1750)
        self.assertTrue(state["wide"])
        self.assertTrue(state["moving"])
        self.assertTrue(state["preparing"])
        self.assertTrue(state["holding"])

    def test_preparation_and_forward_sweep_are_bounded_and_monotonic(self):
        self.servos.command(self.request())
        self.assertTrue(self.channel()["preparing"])
        states = self.finish_motion(limit=12)
        widths = [width for pin, width in self.pulses()]
        self.assertTrue(all(pin == 17 for pin, _ in self.pulses()))
        self.assertTrue(all(1000 <= width <= 1500 for width in widths))
        self.assertEqual(widths[0], 1500)
        turn = widths.index(1000)
        self.assertEqual(widths[-1], 1500)
        self.assertTrue(all(a >= b for a, b in zip(widths[:turn], widths[1:turn + 1])))
        self.assertTrue(all(a <= b for a, b in zip(widths[turn:], widths[turn + 1:])))
        self.assertLessEqual(max(abs(b - a) for a, b in zip(widths, widths[1:])), 4)
        self.assertTrue(any(not state["preparing"] and state["moving"] for state in states))
        self.assertTrue(all(not state["preparing"] or state["moving"] for state in states))
        self.assertEqual(self.channel()["angle"], 90)
        self.assertFalse(self.channel()["preparing"])

    def test_forward_phase_takes_requested_time_and_holds_no_lease_afterward(self):
        self.remember_position(45, wide=True)
        start = self.now
        state = self.servos.command(self.request())["channels"][0]
        self.assertTrue(state["moving"])
        self.assertFalse(state["preparing"])
        self.assertTrue(state["holding"])
        self.finish_motion(limit=6)
        self.assertGreaterEqual(self.now - start, 5)
        self.assertLess(self.now - start, 5.1)
        self.assertEqual(self.channel()["pulseWidthMicros"], 1500)
        self.assertTrue(self.channel()["active"])
        self.assertFalse(self.channel()["holding"])
        for _ in range(2):
            self.now += 1
            self.servos.command({"action": "keepalive", "lease": LEASE})
        self.assertTrue(self.channel()["active"])
        self.now += 1.01
        self.servos.tick()
        self.assertFalse(self.channel()["active"])
        self.assertEqual(self.pulses()[-1], (17, 0))

    def test_tick_does_not_advance_before_due_or_catch_up_after_a_stall(self):
        self.servos.command(self.request())
        count = len(self.pulses())
        self.now += .005
        self.servos.tick()
        self.assertEqual(len(self.pulses()), count)
        self.now += .016
        self.servos.tick()
        self.assertLessEqual(len(self.pulses()) - count, 1)
        before = self.channel()["pulseWidthMicros"]
        count = len(self.pulses())
        self.now += .75  # Still within the lease, but many 20 ms intervals elapsed.
        self.servos.tick()
        self.assertLessEqual(len(self.pulses()) - count, 1)
        self.assertLessEqual(abs(self.channel()["pulseWidthMicros"] - before), 4)
        count = len(self.pulses())
        self.servos.tick()
        self.assertEqual(len(self.pulses()), count)

    def test_read_status_and_foreign_keepalive_cannot_extend_sweep_lease(self):
        self.servos.command(self.request())
        for _ in range(5):
            self.now += 1
            self.servos.command({"action": "status"})
            self.servos.command({"action": "keepalive", "lease": FOREIGN_LEASE})
        self.assertFalse(self.channel()["active"])
        self.assertFalse(self.channel()["moving"])
        self.assertFalse(self.channel()["preparing"])
        count = len(self.driver.calls)
        self.servos.command({"action": "keepalive", "lease": LEASE})
        self.now += 1
        self.servos.tick()
        self.assertEqual(len(self.driver.calls), count)

    def test_release_and_stop_cancel_motion_despite_late_keepalive(self):
        for command in ({"action": "release", "channel": 1}, {"action": "stop"}):
            with self.subTest(command=command):
                self.remember_position(90)
                self.servos.command(self.request())
                self.now += .25
                self.servos.tick()
                self.servos.command(command)
                self.assertFalse(self.channel()["moving"])
                self.assertFalse(self.channel()["preparing"])
                self.assertFalse(self.channel()["active"])
                count = len(self.driver.calls)
                for _ in range(6):
                    self.now += 1
                    self.servos.command({"action": "keepalive", "lease": LEASE})
                    self.servos.tick()
                self.assertEqual(len(self.driver.calls), count)

    def test_manual_move_replaces_sweep_and_old_lease_cannot_revive_it(self):
        self.servos.command(self.request())
        self.now += .5
        self.servos.command({"action": "move", "channel": 1, "angle": 120})
        self.assertFalse(self.channel()["moving"])
        self.assertFalse(self.channel()["preparing"])
        self.assertFalse(self.channel()["holding"])
        self.assertEqual(self.channel()["angle"], 120)
        self.driver.calls.clear()
        for _ in range(4):
            self.now += 1
            self.servos.command({"action": "keepalive", "lease": LEASE})
            self.servos.tick()
        self.assertEqual(self.driver.calls, [("pulse", 17, 0)])
        self.assertFalse(self.channel()["active"])

    def test_gpio_write_error_during_trajectory_propagates(self):
        self.servos.command(self.request())
        def failed_pulse(pin, width):
            raise OSError("injected GPIO write failure")
        self.driver.pulse = failed_pulse
        # Allow enough early steps to leave the rounded starting pulse.
        with self.assertRaisesRegex(OSError, "injected GPIO write failure"):
            for _ in range(30):
                self.now += .020001
                self.servos.tick()


if __name__ == "__main__":
    unittest.main()
