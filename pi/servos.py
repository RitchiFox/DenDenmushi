"""Three SG92R servos, physical 11/13/15 (BCM 17/27/22).

The camera HTTP process uses a private Unix socket. The GPIO broker runs as
the owner with kernel GPIO access; it never touches DMA, PCM or raw memory.
Test moves expire after three seconds. Holding requires a matching Mac lease
renewed within four seconds. Startup and reconnect never start movement.
The kernel PWM backend uses an independent systemd watchdog and stop helper.
Leased sweeps advance bounded positions locally, without per-step network input.
The legacy lgpio backend keeps its finite pulse groups for local regression tests.
"""
import json
import math
import os
from collections import deque
from pathlib import Path
import signal
import socket
import time
import uuid

PINS = (17, 27, 22)
PHYSICAL = (11, 13, 15)
SOCKET = "/run/denden-servos/control.sock"
MIN_ANGLE, MAX_ANGLE = 45, 135
MOVE_SECONDS = 3.0
HOLD_LEASE_SECONDS = 4.0
SERVO_HZ = 50
GROUP_CYCLES = 2
MAX_QUEUED_GROUPS = 3
STEP_MICROS = 10
SWEEP_INTERVAL = 1.0 / SERVO_HZ
# User-verified mounted mechanism, 2026-09-28: nominal12.33..45deg; user extends upper limit to45.5deg.
SECOND_MIN_PULSE, SECOND_MAX_PULSE = 533, 867  # Existing tested commands3deg/33deg, rounded to microseconds.
FIRST_MIN_PULSE = 637
FIRST_MAX_PULSE = 1005  # 45.45deg: do not round beyond requested45.5.


def validate(body):
    if not isinstance(body, dict):
        raise ValueError("invalid_request")
    action = body.get("action")
    if action in ("arm_third", "move_third"):
        fields = {"action", "angle"} if action == "arm_third" else {"action", "angle", "expectedPulse"}
        if (set(body) != fields or type(body["angle"]) not in (int, float)
                or not math.isfinite(body["angle"]) or not 30 <= body["angle"] <= 60):
            raise ValueError("invalid_request")
        if action == "move_third" and (type(body["expectedPulse"]) is not int or not 833 <= body["expectedPulse"] <= 1167):
            raise ValueError("invalid_request")
        return
    if action == "move_second":
        if (set(body) != {"action", "angle", "expectedPulse"}
                or type(body["angle"]) not in (int, float) or not math.isfinite(body["angle"]) or not 3 <= body["angle"] <= 33
                or type(body["expectedPulse"]) is not int or not SECOND_MIN_PULSE <= body["expectedPulse"] <= SECOND_MAX_PULSE):
            raise ValueError("invalid_request")
        return
    if action == "arm_second":
        if set(body) != {"action", "angle"} or type(body["angle"]) not in (int, float) or not math.isfinite(body["angle"]) or not 3 <= body["angle"] <= 33:
            raise ValueError("invalid_request")
        return
    if action == "step_second":
        if (set(body) != {"action", "delta", "expectedPulse"} or type(body["delta"]) is not int or body["delta"] not in (-1, 1)
                or type(body["expectedPulse"]) is not int or not SECOND_MIN_PULSE <= body["expectedPulse"] <= SECOND_MAX_PULSE):
            raise ValueError("invalid_request")
        return
    if action == "arm_first":
        if set(body) not in ({"action"}, {"action", "angle"}):
            raise ValueError("invalid_request")
        if "angle" in body:
            first_pulse(body["angle"])
        return
    if action in ("smooth_first", "smooth_second", "smooth_third"):
        converter = third_pulse if action == "smooth_third" else second_pulse if action == "smooth_second" else first_pulse
        lower, upper = (833, 1167) if action == "smooth_third" else (SECOND_MIN_PULSE, SECOND_MAX_PULSE) if action == "smooth_second" else (FIRST_MIN_PULSE, FIRST_MAX_PULSE)
        if set(body) != {"action", "expectedPulse", "startAngle", "endAngle", "durationSeconds", "lease"}:
            raise ValueError("invalid_request")
        for field in ("startAngle", "endAngle"):
            converter(body[field])
        seconds = body["durationSeconds"]
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or seconds != 0.5 or type(body["expectedPulse"]) is not int
                or not lower <= body["expectedPulse"] <= upper):
            raise ValueError("invalid_request")
        validate_lease(body["lease"])
        return
    if action == "step_first":
        if (set(body) != {"action", "delta", "expectedPulse"}
                or type(body["delta"]) is not int or body["delta"] not in (-1, 1)
                or type(body["expectedPulse"]) is not int
                or not FIRST_MIN_PULSE <= body["expectedPulse"] <= FIRST_MAX_PULSE):
            raise ValueError("invalid_request")
        return
    if action == "sweep":
        if set(body) != {"action", "channel", "startAngle", "endAngle", "durationSeconds", "wide", "lease"}:
            raise ValueError("invalid_request")
        if type(body["channel"]) is not int or body["channel"] != 1:
            raise ValueError("invalid_channel")
        if type(body["wide"]) is not bool:
            raise ValueError("invalid_range")
        validate_lease(body["lease"])
        for field, lower, upper in (("startAngle", MIN_ANGLE, MAX_ANGLE),
                                    ("endAngle", MIN_ANGLE, MAX_ANGLE),
                                    ("durationSeconds", 2, 15)):
            value = body[field]
            if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
                raise ValueError("invalid_sweep")
        return
    if action == "keepalive" and set(body) == {"action", "lease"}:
        validate_lease(body["lease"])
        return
    if action in ("status", "stop") and set(body) == {"action"}:
        return
    fields = {"action", "channel", "angle"} if action == "move" else {"action", "channel"}
    if action == "move" and "lease" in body:
        fields.add("lease")
        validate_lease(body["lease"])
    if action == "move" and "wide" in body:
        fields.add("wide")
        if type(body["wide"]) is not bool:
            raise ValueError("invalid_range")
    if action not in ("move", "release") or set(body) != fields:
        raise ValueError("invalid_request")
    if type(body["channel"]) is not int or body["channel"] not in (1, 2, 3):
        raise ValueError("invalid_channel")
    if action == "move":
        if body.get("wide", False) and body["channel"] != 1:
            raise ValueError("invalid_range")
        angle = body["angle"]
        if (type(angle) not in (int, float) or not MIN_ANGLE <= angle <= MAX_ANGLE
                or not math.isfinite(angle)):
            raise ValueError("invalid_angle")


def validate_lease(value):
    if not isinstance(value, str) or len(value) != 36 or str(uuid.UUID(value)) != value:
        raise ValueError("invalid_lease")


def first_pulse(angle):
    if type(angle) not in (int, float) or not math.isfinite(angle) or not 12.3 <= angle <= 45.5:
        raise ValueError("first_servo_limit")
    return max(FIRST_MIN_PULSE, min(FIRST_MAX_PULSE, round(500 + angle * 1000 / 90)))


def second_pulse(angle):
    if type(angle) not in (int, float) or not math.isfinite(angle) or not 3 <= angle <= 33:
        raise ValueError("second_servo_limit")
    return max(SECOND_MIN_PULSE, min(SECOND_MAX_PULSE, round(500 + angle * 2000 / 180)))


def second_angle(pulse):
    return max(3, min(33, (pulse-500)*180/2000))


def third_pulse(angle):
    if type(angle) not in (int, float) or not math.isfinite(angle) or not 30 <= angle <= 60:
        raise ValueError("third_servo_limit")
    return round(500 + angle * 2000 / 180)


class FirstSweep:
    """Bounded eased legs, executed locally with the existing expiring lease."""
    def __init__(self, pulse, body, second=False, third=False):
        lower, upper = (833, 1167) if third else (SECOND_MIN_PULSE, SECOND_MAX_PULSE) if second else (FIRST_MIN_PULSE, FIRST_MAX_PULSE)
        if not lower <= pulse <= upper:
            raise ValueError("second_servo_position_changed" if second else "first_servo_position_changed")
        converter = third_pulse if third else second_pulse if second else first_pulse
        self.steps = deque()
        start, end = converter(body["startAngle"]), converter(body["endAngle"])
        def leg(a, b, seconds, preparing):
            count = max(1, math.ceil(seconds / SWEEP_INTERVAL))
            for i in range(1, count + 1):
                t = i / count
                width = b if i == count else round(a + (b-a) * t*t*(3-2*t))
                self.steps.append((width, max(30, min(60, (width-500)*180/2000)) if third else second_angle(width) if second else (width-500)*90/1000, preparing))
        if pulse != start:
            leg(pulse, start, 0.5, True)
        leg(start, end, 0.5, False)
        self.preparing = self.steps[0][2]
        self.next_at = 0.0


class Sweep:
    """A finite local trajectory; clock stalls never skip queued positions."""
    def __init__(self, pulse, body):
        wide = body["wide"]
        lower, upper = (1000, 2000) if wide else (1250, 1750)
        if not lower <= pulse <= upper:
            raise ValueError("invalid_start_pulse")
        span = 2000 if wide else 1000
        current_angle = 90 + (pulse - 1500) * 180 / span
        self.steps = deque()

        def leg(start, end, seconds, preparing):
            count = max(1, math.ceil(seconds / SWEEP_INTERVAL))
            for step in range(1, count + 1):
                fraction = step / count
                eased = fraction * fraction * (3 - 2 * fraction)
                angle = end if step == count else start + (end - start) * eased
                width = round(1500 + (angle - 90) * span / 180)
                self.steps.append((width, angle, preparing))

        distance = abs(current_angle - body["startAngle"])
        if distance > 0:
            leg(current_angle, body["startAngle"], distance / 9, True)
        leg(body["startAngle"], body["endAngle"], body["durationSeconds"], False)
        self.preparing = self.steps[0][2]
        self.initial_angle = current_angle
        self.next_at = 0.0


class Servos:
    """Single-threaded state machine; a read never renews the output deadline."""
    def __init__(self, driver, clock=time.monotonic, first_manual=False):
        self.driver, self.clock = driver, clock
        self.first_manual = first_manual
        self.angles = [None] * 3
        self.active = [False] * 3
        self.deadlines = [0.0] * 3
        self.leases = [None] * 3
        self.widths = [0] * 3
        self.wide = [False] * 3
        self.sweeps = [None] * 3

    def release(self, index):
        # Clear future points even if disabling this output raises. serve()
        # treats a GPIO failure as fatal and runs independent cleanup too.
        self.sweeps[index] = None
        if self.active[index]:
            self.driver.pulse(PINS[index], 0)
            self.active[index] = False
            self.deadlines[index] = 0
            self.leases[index] = None

    def stop(self):
        # Attempt every channel even if one hardware write fails.
        failures = []
        for index in range(3):
            try:
                self.release(index)
            except OSError as exc:
                failures.append(exc)
        if failures:
            raise failures[0]

    def tick(self):
        for index in range(3):
            if self.active[index] and self.clock() >= self.deadlines[index]:
                self.release(index)
                continue
            sweep = self.sweeps[index]
            if sweep is not None and self.clock() >= sweep.next_at:
                width, angle, preparing = sweep.steps.popleft()
                if width != self.widths[index]:
                    self.driver.pulse(PINS[index], width)
                self.widths[index] = width
                self.angles[index] = angle
                sweep.preparing = preparing
                # Schedule after I/O. Never emit a burst of catch-up steps
                # after a late loop iteration or slow sysfs operation.
                sweep.next_at = self.clock() + SWEEP_INTERVAL
                if not sweep.steps:
                    self.sweeps[index] = None
                    self.leases[index] = None
                    self.deadlines[index] = self.clock() + (0.5 if self.first_manual else MOVE_SECONDS)
        self.driver.tick()

    def status(self):
        return {"ok": True, "driver": getattr(self.driver, "name", "lgpio"),
                "minAngle": MIN_ANGLE, "maxAngle": MAX_ANGLE,
                "firstManual": self.first_manual, "firstSmooth": self.first_manual, "secondManual": self.first_manual, "secondSlider": self.first_manual, "secondSmooth": self.first_manual, "thirdCalibration": self.first_manual, "fixedMotionSeconds": 0.5, "thirdMinPulse": 833, "thirdMaxPulse": 1167,
                "secondMinPulse": SECOND_MIN_PULSE, "secondMaxPulse": SECOND_MAX_PULSE,
                "firstMinPulse": FIRST_MIN_PULSE, "firstMaxPulse": FIRST_MAX_PULSE,
                "releaseAfterSeconds": MOVE_SECONDS, "holdTimeoutSeconds": HOLD_LEASE_SECONDS,
                "channels": [{"channel": i + 1, "gpio": PINS[i], "pin": PHYSICAL[i],
                              "angle": self.angles[i], "active": self.active[i],
                              "pulseWidthMicros": self.widths[i], "wide": self.wide[i],
                              "moving": self.sweeps[i] is not None,
                              "preparing": self.sweeps[i].preparing if self.sweeps[i] else False,
                              "holding": self.active[i] and self.leases[i] is not None}
                             for i in range(3)]}

    def command(self, body):
        validate(body)
        action = body["action"]
        if action == "smooth_third":
            if not self.first_manual or getattr(self.driver, "name", None) != "kernel-pwm":
                raise ValueError("third_servo_calibration_only")
            old = self.widths[2]
            if old == 0 or body["expectedPulse"] != old:
                raise ValueError("third_servo_position_changed")
            if self.sweeps[2] is not None:
                raise ValueError("third_servo_busy")
            sweep = FirstSweep(old, body, third=True)
            if not self.active[2]:
                self.driver.pulse(PINS[2], old)
            self.active[2], self.wide[2] = True, True
            self.leases[2] = body["lease"]
            self.deadlines[2] = self.clock() + HOLD_LEASE_SECONDS
            sweep.next_at = self.clock() + SWEEP_INTERVAL
            self.sweeps[2] = sweep
            return self.status()
        if self.first_manual and action == "move" and body["channel"] == 3:
            raise ValueError("third_servo_calibration_only")
        if action in ("arm_third", "move_third"):
            if not self.first_manual or getattr(self.driver, "name", None) != "kernel-pwm":
                raise ValueError("third_servo_calibration_only")
            pulse = round(500 + body["angle"] * 2000 / 180)
            if action == "arm_third":
                if self.widths[2] != 0:
                    raise ValueError("third_servo_already_initialized")
                self.widths[2], self.angles[2], self.wide[2] = pulse, max(30, min(60, (pulse-500)*180/2000)), True
                return self.status()
            if self.widths[2] == 0 or body["expectedPulse"] != self.widths[2]:
                raise ValueError("third_servo_position_changed")
            self.driver.pulse(PINS[2], pulse)
            self.widths[2], self.angles[2], self.wide[2] = pulse, max(30, min(60, (pulse-500)*180/2000)), True
            self.active[2], self.leases[2], self.sweeps[2] = True, None, None
            self.deadlines[2] = self.clock() + 0.5
            return self.status()
        if self.first_manual and action == "move" and body["channel"] == 2:
            raise ValueError("second_servo_manual_only")
        if action in ("arm_second", "step_second", "move_second", "smooth_second"):
            if not self.first_manual or getattr(self.driver, "name", None) != "kernel-pwm":
                raise ValueError("second_servo_manual_only")
            if action == "arm_second":
                if self.widths[1] != 0:
                    raise ValueError("second_servo_already_initialized")
                pulse = second_pulse(body["angle"])
                self.widths[1], self.angles[1], self.wide[1] = pulse, second_angle(pulse), True
                return self.status()
            old = self.widths[1]
            if old == 0 or body["expectedPulse"] != old:
                raise ValueError("second_servo_position_changed")
            if action == "smooth_second":
                if self.sweeps[1] is not None:
                    raise ValueError("second_servo_busy")
                sweep = FirstSweep(old, body, second=True)
                if not self.active[1]:
                    self.driver.pulse(PINS[1], old)
                self.active[1], self.wide[1] = True, True
                self.leases[1] = body["lease"]
                self.deadlines[1] = self.clock() + HOLD_LEASE_SECONDS
                sweep.next_at = self.clock() + SWEEP_INTERVAL
                self.sweeps[1] = sweep
                return self.status()
            pulse = second_pulse(body["angle"]) if action == "move_second" else max(SECOND_MIN_PULSE, min(SECOND_MAX_PULSE, old + 11 * body["delta"]))
            if pulse == old:
                raise ValueError("second_servo_limit")
            self.driver.pulse(PINS[1], pulse)
            self.widths[1], self.angles[1] = pulse, second_angle(pulse)
            self.wide[1], self.active[1] = True, True
            self.leases[1], self.sweeps[1] = None, None
            self.deadlines[1] = self.clock() + 0.5
            return self.status()
        # Mounted mechanism: reject legacy sliders/sweeps, including old clients.
        if self.first_manual and (action == "sweep" or
                                  (action == "move" and body["channel"] == 1)):
            raise ValueError("first_servo_manual_only")
        if action in ("arm_first", "step_first", "smooth_first"):
            if not self.first_manual or getattr(self.driver, "name", None) != "kernel-pwm":
                raise ValueError("first_servo_manual_only")
            if action == "arm_first":
                # User explicitly confirms the current physical pose. No pulse.
                if self.widths[0] != 0:
                    raise ValueError("first_servo_already_initialized")
                pulse = first_pulse(body.get("angle", 45))
                self.widths[0], self.angles[0], self.wide[0] = pulse, (pulse-500)*90/1000, True
                return self.status()
            old = self.widths[0]
            if old == 0 or body["expectedPulse"] != old:
                raise ValueError("first_servo_position_changed")
            if action == "smooth_first":
                if self.sweeps[0] is not None:
                    raise ValueError("first_servo_busy")
                sweep = FirstSweep(old, body)
                if not self.active[0]:
                    self.driver.pulse(PINS[0], old)
                self.active[0], self.wide[0] = True, True
                self.leases[0] = body["lease"]
                self.deadlines[0] = self.clock() + HOLD_LEASE_SECONDS
                sweep.next_at = self.clock() + SWEEP_INTERVAL
                self.sweeps[0] = sweep
                return self.status()
            pulse = max(FIRST_MIN_PULSE, min(FIRST_MAX_PULSE, old + 11 * body["delta"]))
            if pulse == old:
                raise ValueError("first_servo_limit")
            # <=11 us per deliberate click, no hold, trajectory or replay.
            # Compare-and-set prevents duplicate delivery from adding a step.
            self.driver.pulse(PINS[0], pulse)
            self.widths[0] = pulse
            self.angles[0] = (pulse - 500) * 90 / 1000
            self.wide[0], self.active[0] = True, True
            self.leases[0], self.sweeps[0] = None, None
            self.deadlines[0] = self.clock() + 0.5
            return self.status()
        self.tick()
        if action == "stop":
            self.stop()
        elif action == "keepalive":
            for index in range(3):
                if self.active[index] and self.leases[index] == body["lease"]:
                    self.driver.extend(PINS[index], self.widths[index])
                    self.deadlines[index] = self.clock() + HOLD_LEASE_SECONDS
        elif action == "release":
            self.release(body["channel"] - 1)
        elif action == "sweep":
            if getattr(self.driver, "name", None) != "kernel-pwm":
                raise ValueError("sweep_requires_kernel_pwm")
            # The remembered width is not shaft feedback. Refuse an unknown
            # starting point instead of jumping to a manufactured initial angle.
            sweep = Sweep(self.widths[0], body)
            self.sweeps[0] = None
            if not self.active[0]:
                self.driver.pulse(PINS[0], self.widths[0])
            self.active[0] = True
            self.angles[0] = sweep.initial_angle
            self.wide[0] = body["wide"]
            self.leases[0] = body["lease"]
            self.deadlines[0] = self.clock() + HOLD_LEASE_SECONDS
            sweep.next_at = self.clock() + SWEEP_INTERVAL
            self.sweeps[0] = sweep
        elif action == "move":
            index, angle = body["channel"] - 1, body["angle"]
            # The first channel can opt into 1000–2000 us; the default
            # remains 1250–1750 us. Labels are nominal, not shaft feedback.
            wide = body.get("wide", False)
            pulse = round(1500 + (angle - 90) * (2000 if wide else 1000) / 180)
            self.sweeps[index] = None
            self.driver.pulse(PINS[index], pulse)
            self.angles[index] = angle
            self.active[index] = True
            self.widths[index] = pulse
            self.wide[index] = wide
            self.leases[index] = body.get("lease")
            self.deadlines[index] = self.clock() + (HOLD_LEASE_SECONDS if self.leases[index] else MOVE_SECONDS)
        return self.status()


def reject_tft_drivers(root=Path("/sys/bus/spi")):
    """Never detach a live kernel display driver to acquire servo pins.

    Build22 lost network connectivity during startup, before we could establish
    which initialization step failed. The removed TFT must be disabled in boot
    configuration and the Pi rebooted before trying GPIO17. Otherwise refuse.
    """
    for device in ("spi0.0", "spi0.1"):
        driver = root / "devices" / device / "driver"
        if driver.exists() and driver.resolve().name in ("ads7846", "fb_ili9486"):
            raise OSError("tft_driver_still_active_disable_overlay_and_reboot")


class LgpioDriver:
    """Short prototype movements, with finite software-timed pulse trains.

    Timing is not a calibrated position guarantee. Kernel line ownership avoids
    taking pins from other drivers, and no movement is generated by claiming.
    """
    def __init__(self, lib=None, model_path=Path("/proc/device-tree/model"),
                 spi_root=Path("/sys/bus/spi")):
        model = model_path.read_text().rstrip("\0")
        if not model.startswith("Raspberry Pi 3 Model B "):
            raise OSError("unsupported_pi_model")
        reject_tft_drivers(spi_root)
        # Import only in the broker: lgpio starts a notification thread and
        # creates a pipe in WorkingDirectory, not in the HTTP service.
        if lib is None:
            import lgpio as lib
        self.lib, self.handle = lib, None
        self.claimed = []
        self.queue_capacity = {}
        self.targets = {}
        self.last_enqueued_width = {}
        try:
            self.handle = self._call("gpiochip_open", 0)
            info = self._call("gpio_get_chip_info", self.handle)
            if info[0] != 0 or info[1] != 54 or info[3] != "pinctrl-bcm2835":
                raise OSError("unexpected_gpiochip")
            for pin in PINS:
                self._call("gpio_claim_output", self.handle, pin, 0)
                self.claimed.append(pin)
                self.queue_capacity[pin] = self._call("tx_room", self.handle, pin, self.lib.TX_PWM)
            print("servos: kernel GPIO ready; all outputs low", flush=True)
        except Exception:
            self.close()
            raise

    def _call(self, name, *args, allow_error=None):
        try:
            result = getattr(self.lib, name)(*args)
        except self.lib.error as exc:
            if allow_error is not None and exc.args == (self.lib.error_text(allow_error),):
                return 0
            raise OSError("servo_gpio_operation_failed") from exc
        if isinstance(result, int) and result < 0:
            if result == allow_error:
                return 0
            raise OSError("servo_gpio_operation_failed")
        return result

    def _stop_pulses(self, pin):
        # lgpio 0.2.2 reports BAD_PWM_MICROS for (0,0) if no pulse train
        # exists (first move, or a finite train already expired). Only for
        # this exact stop call that result means the channel is already idle.
        self._call("tx_pulse", self.handle, pin, 0, 0,
                   allow_error=self.lib.BAD_PWM_MICROS)

    def pulse(self, pin, width):
        if (type(pin) is not int or pin not in PINS or type(width) is not int
                or (width != 0 and not 1000 <= width <= 2000)):
            raise ValueError("invalid_pulse")
        if width == 0:
            # Clear intent before GPIO operations so a failed stop cannot be
            # followed by an accidental refill. A released shaft is unknown.
            self.targets.pop(pin, None)
            self.last_enqueued_width.pop(pin, None)
            self._stop_pulses(pin)
            self._call("gpio_write", self.handle, pin, 0)
            return
        # Retargeting never cuts an active high pulse. Already submitted
        # groups finish before the new target is approached at a cycle edge.
        self.targets[pin] = width
        self.tick()

    def tick(self):
        for pin, target in self.targets.items():
            room = self._call("tx_room", self.handle, pin, self.lib.TX_PWM)
            queued = self.queue_capacity[pin] - room
            # At most 3 x 2 cycles = 120 ms of requested output can remain
            # if the broker stalls, including during a leased hold.
            for _ in range(max(0, min(room, MAX_QUEUED_GROUPS - queued))):
                previous = self.last_enqueued_width.get(pin, target)
                width = previous + max(-STEP_MICROS, min(STEP_MICROS, target - previous))
                self._call("tx_servo", self.handle, pin, width, SERVO_HZ, 0, GROUP_CYCLES)
                self.last_enqueued_width[pin] = width

    def extend(self, pin, width):
        if type(pin) is not int or pin not in PINS or type(width) is not int or not 1000 <= width <= 2000:
            raise ValueError("invalid_pulse")
        # A keepalive can maintain an existing target but never create one,
        # retarget a newer move or add a long train behind the active output.
        if self.targets.get(pin) == width:
            self.tick()

    def close(self):
        self.targets.clear()
        self.last_enqueued_width.clear()
        if self.handle is None:
            return
        try:
            for pin in self.claimed:
                try:
                    self._stop_pulses(pin)
                except OSError:
                    pass
                try:
                    self._call("gpio_write", self.handle, pin, 0)
                except OSError:
                    pass
        finally:
            handle, self.handle = self.handle, None
            self._call("gpiochip_close", handle)
            self.claimed.clear()


class ServoClient:
    def __init__(self, path=SOCKET):
        self.path = path

    def command(self, body):
        validate(body)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                client.connect(self.path)
                client.sendall(json.dumps(body, allow_nan=False).encode() + b"\n")
                with client.makefile("rb") as stream:
                    result = stream.readline(4097)
                if len(result) > 4096:
                    raise ValueError("oversize response")
                return json.loads(result)
        except (OSError, ValueError):
            return {"ok": False, "error": "servo_service_unavailable"}


def _client_command(client, servos):
    """A slow/disconnected socket is not a GPIO hardware failure."""
    try:
        with client.makefile("rb") as stream:
            raw = stream.readline(513)
    except OSError:
        return {"ok": False, "error": "invalid_request"}
    try:
        if len(raw) > 512 or not raw.endswith(b"\n"):
            raise ValueError("invalid_request")
        return servos.command(json.loads(raw))
    except ValueError as exc:
        code = str(exc)
        return {"ok": False, "error": code if code in ("invalid_start_pulse", "sweep_requires_kernel_pwm", "first_servo_manual_only", "first_servo_already_initialized", "first_servo_position_changed", "first_servo_limit", "first_servo_busy", "second_servo_manual_only", "second_servo_already_initialized", "second_servo_position_changed", "second_servo_limit", "second_servo_busy", "third_servo_calibration_only", "third_servo_already_initialized", "third_servo_position_changed", "third_servo_limit", "third_servo_busy") else "invalid_request"}
    # GPIO OSError is deliberately propagated; serve() then closes outputs.


class SystemdWatchdog:
    """Renew only from the command loop: a blocked loop must lose its watchdog."""
    def __init__(self, required=False, environment=None, clock=time.monotonic):
        environment = os.environ if environment is None else environment
        self.address = environment.get("NOTIFY_SOCKET")
        self.clock, self.next_ping = clock, 0.0
        usec = environment.get("WATCHDOG_USEC", "0")
        pid = environment.get("WATCHDOG_PID")
        if not usec.isdigit() or (pid is not None and pid != str(os.getpid())):
            raise OSError("invalid_servo_watchdog")
        self.interval = int(usec) / 3_000_000
        if required and (not self.address or not 0.03 <= self.interval <= 0.5):
            raise OSError("kernel_pwm_requires_systemd_watchdog")
        if self.address and self.address.startswith("@"):
            self.address = "\0" + self.address[1:]

    def notify(self, message):
        if self.address:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                client.settimeout(0.1)
                client.sendto(message.encode(), self.address)

    def ready(self):
        self.notify("READY=1")
        self.tick()

    def tick(self):
        if self.address and self.interval > 0 and self.clock() >= self.next_ping:
            self.notify("WATCHDOG=1")
            self.next_ping = self.clock() + self.interval


def serve():
    running = True
    def shutdown(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    backend = os.environ.get("DENDEN_SERVO_DRIVER", "lgpio")
    if backend not in ("lgpio", "kernel-pwm"):
        raise OSError("invalid_servo_driver")
    watchdog = SystemdWatchdog(required=backend == "kernel-pwm")
    if backend == "kernel-pwm":
        from servo_pwm import KernelPwmDriver
        driver = KernelPwmDriver()
    else:
        driver = LgpioDriver()
    servos = Servos(driver, first_manual=True)
    path = Path(SOCKET)
    try:
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            os.chmod(path, 0o660)
            listener.listen(8)
            listener.settimeout(0.01)
            watchdog.ready()
            while running:
                servos.tick()
                watchdog.tick()
                try:
                    client, _ = listener.accept()
                except socket.timeout:
                    continue
                with client:
                    client.settimeout(0.02)
                    result = _client_command(client, servos)
                    try:
                        client.sendall(json.dumps(result, allow_nan=False).encode() + b"\n")
                    except OSError:
                        pass
    finally:
        driver.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    serve()
