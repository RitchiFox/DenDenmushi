"""Kernel GPIO PWM for three prepared DenDenMushi servo channels.

This module neither exports PWM channels nor loads overlays. A privileged
startup helper must prepare disabled channels and grant the broker access to
enable/duty_cycle. Kernel PWM continues if the broker stalls; systemd's
watchdog and ExecStopPost stop command provide the independent shutdown.
"""
import argparse
from pathlib import Path
import struct
import sys

PINS = (17, 27, 22)
PERIOD_NS = 20_000_000
SYSFS_ROOT = Path("/sys/class/pwm")
GPIO_NODE = Path("/proc/device-tree/soc/gpio@7e200000")


def _matches(sysfs_root, gpio_node):
    raw = (Path(gpio_node) / "phandle").read_bytes()
    if len(raw) != 4:
        raise OSError("invalid_gpio_controller_phandle")
    controller, = struct.unpack(">I", raw)
    if controller in (0, 0xffffffff):
        raise OSError("invalid_gpio_controller_phandle")
    matches = []
    for chip in sorted(Path(sysfs_root).glob("pwmchip*")):
        if not chip.name.removeprefix("pwmchip").isdigit():
            continue
        try:
            node = chip / "device" / "of_node"
            if ((chip / "npwm").read_text().strip() != "1"
                    or (chip / "device" / "driver").resolve(strict=True).name != "pwm-gpio"
                    or (node / "compatible").read_bytes() != b"pwm-gpio\0"):
                continue
            cells = (node / "gpios").read_bytes()
            if len(cells) != 12:
                continue
            phandle, pin, flags = struct.unpack(">III", cells)
            if phandle == controller and pin in PINS and flags == 0:
                matches.append((pin, chip))
        except (OSError, UnicodeError):
            # Unidentified/foreign channels must never be written to.
            continue
    return matches


def discover(sysfs_root=SYSFS_ROOT, gpio_node=GPIO_NODE):
    """Return only positively identified GPIO-to-pwmchip mappings, no writes."""
    channels = {}
    for pin, chip in _matches(sysfs_root, gpio_node):
        if pin in channels:
            raise OSError("duplicate_pwm_gpio")
        channels[pin] = chip
    return channels


def _write(path, value):
    path.write_text(f"{value}\n")


def _disable(channels):
    """Try every known exported channel even if another write fails."""
    failures = []
    for chip in channels:
        pwm = chip / "pwm0"
        if not pwm.exists():
            continue
        for attribute in ("enable", "duty_cycle"):
            path = pwm / attribute
            try:
                # Fresh exports can have period=0. Some kernel versions
                # reject even redundant disabled writes in that state.
                if path.read_text().strip() == "0":
                    continue
            except (OSError, UnicodeError):
                pass
            try:
                _write(path, 0)
            except OSError as exc:
                failures.append(exc)
    if failures:
        raise OSError("servo_pwm_stop_failed") from failures[0]


def stop_all(sysfs_root=SYSFS_ROOT, gpio_node=GPIO_NODE):
    """Stop identified channels, including partial preparation or duplicates."""
    _disable([chip for _, chip in _matches(sysfs_root, gpio_node)])


class KernelPwmDriver:
    name = "kernel-pwm"

    def __init__(self, sysfs_root=SYSFS_ROOT, gpio_node=GPIO_NODE):
        self.channels = {}
        self.targets = {}
        self.enabled = set()
        self.closed = False
        known = _matches(sysfs_root, gpio_node)
        try:
            for pin, chip in known:
                if pin in self.channels:
                    raise OSError("duplicate_pwm_gpio")
                self.channels[pin] = chip
            if set(self.channels) != set(PINS):
                raise OSError("servo_pwm_channels_not_prepared")
            for chip in self.channels.values():
                pwm = chip / "pwm0"
                if ((pwm / "enable").read_text().strip() != "0"
                        or (pwm / "period").read_text().strip() != str(PERIOD_NS)
                        or (pwm / "duty_cycle").read_text().strip() != "0"
                        or (pwm / "polarity").read_text().strip() != "normal"):
                    raise OSError("servo_pwm_channel_not_idle")
        except Exception:
            self.closed = True
            try:
                _disable([chip for _, chip in known])
            except OSError:
                pass
            raise

    @staticmethod
    def _validate(pin, width, allow_zero=True):
        if (type(pin) is not int or pin not in PINS or type(width) is not int
                or not ((allow_zero and width == 0) or 500 <= width <= (2000 if pin == 17 else 2500))):
            raise ValueError("invalid_pulse")

    def pulse(self, pin, width):
        self._validate(pin, width)
        if self.closed:
            raise OSError("servo_pwm_driver_closed")
        pwm = self.channels[pin] / "pwm0"
        if width == 0:
            self.targets.pop(pin, None)
            try:
                _write(pwm / "enable", 0)
            finally:
                self.enabled.discard(pin)
                _write(pwm / "duty_cycle", 0)
            return
        # pwm-gpio applies an enabled duty change at the next period boundary.
        # Do not disable/re-enable a running channel or truncate its high pulse.
        _write(pwm / "duty_cycle", width * 1000)
        if pin not in self.enabled:
            _write(pwm / "enable", 1)
            self.enabled.add(pin)
        self.targets[pin] = width

    def extend(self, pin, width):
        self._validate(pin, width, allow_zero=False)
        # Lease ownership and expiry live in Servos. Renewal must never start
        # a released output, retarget a newer move or rewrite the PWM state.
        if not self.closed and self.targets.get(pin) == width:
            return

    def tick(self):
        pass

    def close(self):
        self.targets.clear()
        self.enabled.clear()
        self.closed = True
        _disable(self.channels.values())


def main():
    parser = argparse.ArgumentParser(description="Stop known DenDenMushi PWM channels")
    parser.add_argument("action", choices=("stop",))
    parser.parse_args()
    try:
        stop_all()
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
