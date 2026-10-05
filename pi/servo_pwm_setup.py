"""Prepare only the three verified Pi 3B servo PWM channels, initially disabled.

Run as root from the dedicated setup unit, after the old GPIO broker stops.
No boot configuration, GPIO reassignment, movement or general command endpoint
is provided. Device-tree overlays remain until reboot; preparation is idempotent.
"""
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import time

from servo_pwm import discover, stop_all


PINS = (17, 27, 22)
PERIOD_NS = 20_000_000
MODEL = Path("/proc/device-tree/model")
COMPATIBLE = Path("/proc/device-tree/compatible")
SPI_ROOT = Path("/sys/bus/spi")
SYSFS_ROOT = Path("/sys/class/pwm")
GPIO_NODE = Path("/proc/device-tree/soc/gpio@7e200000")
MODPROBE = Path("/sbin/modprobe")
DTOVERLAY_PATHS = tuple(map(Path, (
    "/usr/bin/dtoverlay", "/usr/sbin/dtoverlay", "/sbin/dtoverlay",
    "/opt/vc/bin/dtoverlay",
)))
OVERLAY_DIRECTORY = Path("/boot/firmware/overlays")
COMMAND_TIMEOUT = 8
READINESS_TIMEOUT = 2


class SetupError(Exception):
    pass


def _verify_hardware(model=MODEL, compatible=COMPATIBLE, spi_root=SPI_ROOT):
    try:
        if (not model.read_text().rstrip("\0").startswith("Raspberry Pi 3 Model B ")
                or b"raspberrypi,3-model-b" not in compatible.read_bytes().split(b"\0")):
            raise SetupError("unsupported_pi_model")
        for device in ("spi0.0", "spi0.1"):
            driver = spi_root / "devices" / device / "driver"
            if driver.exists() and driver.resolve().name in ("ads7846", "fb_ili9486"):
                raise SetupError("tft_driver_still_active")
    except OSError:
        raise SetupError("hardware_unverified") from None


def _trusted_file(path, executable=False):
    try:
        info = path.stat()
        return (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022
                and (not executable or bool(info.st_mode & 0o111)))
    except OSError:
        return False


def _overlay_command(pin):
    # Never resolve a root executable through the caller's PATH or accept an
    # overlay path/parameter supplied by an HTTP request or command-line input.
    if pin not in PINS:
        raise SetupError("unsupported_gpio")
    overlay = OVERLAY_DIRECTORY / "pwm-gpio.dtbo"
    if not _trusted_file(overlay):
        raise SetupError("pwm_overlay_unavailable")
    binary = next((path for path in DTOVERLAY_PATHS if _trusted_file(path, executable=True)), None)
    if binary is None:
        raise SetupError("dtoverlay_unavailable")
    return [str(binary), "-d", str(OVERLAY_DIRECTORY), "pwm-gpio", f"gpio={pin}"]


def _run(command, runner):
    try:
        result = runner(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
                        check=False, env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    except (OSError, subprocess.SubprocessError):
        raise SetupError("pwm_setup_command_failed") from None
    if result.returncode != 0:
        raise SetupError("pwm_setup_command_failed")


def _wait(check, clock, sleep):
    deadline = clock() + READINESS_TIMEOUT
    # Both time and attempts are bounded, including under an unexpected clock.
    for _ in range(42):
        value = check()
        if value:
            return value
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(0.05, remaining))
    raise SetupError("pwm_setup_readiness_timeout")


def _configure(chip, account, clock, sleep, chown=os.chown, chmod=os.chmod):
    channel = chip / "pwm0"
    if not channel.exists():
        try:
            (chip / "export").write_text("0")
        except OSError:
            # A channel exported concurrently is acceptable only if it really
            # appears; permission or driver failures still fail closed below.
            if not channel.exists():
                raise
        _wait(lambda: channel.is_dir(), clock, sleep)
    # Some PWM core versions reject every applied state with period zero.
    # A freshly exported, verified disabled/zero-duty channel can safely gain
    # its period first; that write cannot start a pulse train.
    if (channel / "period").read_text().strip() == "0":
        if ((channel / "enable").read_text().strip() != "0"
                or (channel / "duty_cycle").read_text().strip() != "0"):
            raise SetupError("pwm_unconfigured_state_invalid")
        (channel / "period").write_text(str(PERIOD_NS))
    for name, value in (("enable", "0"), ("duty_cycle", "0"),
                        ("period", str(PERIOD_NS)), ("polarity", "normal")):
        (channel / name).write_text(value)
    expected = {"enable": "0", "duty_cycle": "0", "period": str(PERIOD_NS), "polarity": "normal"}
    if any((channel / name).read_text().strip() != value for name, value in expected.items()):
        raise SetupError("pwm_setup_readback_failed")
    # The owner can select a duty and enable only these prepared channels.
    # Export, period, polarity, chip directories and all other GPIO stay root-owned.
    for name in ("enable", "duty_cycle"):
        path = channel / name
        chmod(path, 0o600)
        chown(path, account.pw_uid, account.pw_gid)


def prepare(owner, *, runner=subprocess.run, euid=os.geteuid, account_lookup=pwd.getpwnam,
            verify_hardware=_verify_hardware, sysfs_root=SYSFS_ROOT, gpio_node=GPIO_NODE,
            clock=time.monotonic, sleep=time.sleep, chown=os.chown, chmod=os.chmod):
    if euid() != 0:
        raise SetupError("root_required")
    if not isinstance(owner, str) or re.fullmatch(r"[a-z_][a-z0-9_-]*", owner) is None:
        raise SetupError("invalid_owner")
    try:
        account = account_lookup(owner)
    except KeyError:
        raise SetupError("invalid_owner") from None
    if account.pw_uid < 1000:
        raise SetupError("invalid_owner")
    verify_hardware()
    try:
        # Discovery independently validates the controller phandle and each
        # chip's driver, one-channel count and exact GPIO tuple.
        chips = discover(sysfs_root=sysfs_root, gpio_node=gpio_node)
        stop_all(sysfs_root=sysfs_root, gpio_node=gpio_node)
        if not _trusted_file(MODPROBE, executable=True):
            raise SetupError("modprobe_unavailable")
        _run([str(MODPROBE), "pwm-gpio"], runner)
        for pin in PINS:
            chips = discover(sysfs_root=sysfs_root, gpio_node=gpio_node)
            if pin not in chips:
                _run(_overlay_command(pin), runner)
                chip = _wait(lambda: discover(sysfs_root=sysfs_root, gpio_node=gpio_node).get(pin), clock, sleep)
            else:
                chip = chips[pin]
            _configure(chip, account, clock, sleep, chown, chmod)
        chips = discover(sysfs_root=sysfs_root, gpio_node=gpio_node)
        if set(chips) != set(PINS):
            raise SetupError("pwm_channels_unverified")
        for chip in chips.values():
            channel = chip / "pwm0"
            if (channel / "enable").read_text().strip() != "0" or (channel / "duty_cycle").read_text().strip() != "0":
                raise SetupError("pwm_setup_readback_failed")
        return {"ok": True, "pins": list(PINS), "periodNanoseconds": PERIOD_NS, "enabled": False}
    except Exception:
        try:
            stop_all(sysfs_root=sysfs_root, gpio_node=gpio_node)
        except Exception:
            raise SetupError("pwm_setup_cleanup_failed") from None
        raise


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 2 or argv[0] != "prepare":
            raise SetupError("unsupported_arguments")
        result = prepare(argv[1])
    except Exception as exc:
        code = str(exc) if isinstance(exc, SetupError) else "servo_pwm_prepare_failed"
        print(json.dumps({"ok": False, "error": code}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
