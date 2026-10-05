#!/usr/bin/env python3
"""Disable only DenDen servo startup on an explicitly selected Pi boot volume.

Does not format, mount Linux filesystems, change Wi-Fi, or alter SSH keys.
Default is a read-only preview; --apply requires the inspected boot directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

MASK = "systemd.mask=denden-servos.service"


def plan(cmdline, config):
    line = cmdline.rstrip("\r\n")
    if not line or "\n" in line or "\r" in line or "\0" in line:
        raise ValueError("cmdline.txt must contain one nonempty line")
    if not any(token.startswith("root=") for token in line.split()):
        raise ValueError("Missing root filesystem argument; not a recognized Pi command line")
    if MASK not in line.split():
        line += " " + MASK
    # The owner physically removed this exact display. Keep its line as a
    # comment for rollback, leaving other overlays and settings unchanged.
    lines = config.splitlines(keepends=True)
    for i, value in enumerate(lines):
        if re.match(r"^\s*dtoverlay\s*=\s*tft35a(?=[:,\s]|$)", value):
            lines[i] = "# DenDen removed TFT: " + value
    return {"cmdline.txt": line + "\n", "config.txt": "".join(lines)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boot", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    boot = args.boot.resolve(strict=True)
    original = {}
    for name in ("cmdline.txt", "config.txt"):
        file = boot / name
        if file.is_symlink() or not file.is_file():
            raise SystemExit("Expected a regular " + name)
        original[name] = file.read_text()
    changes = plan(original["cmdline.txt"], original["config.txt"])
    summary = {name: {"changed": value != original[name],
                      "beforeSHA256": hashlib.sha256(original[name].encode()).hexdigest(),
                      "afterSHA256": hashlib.sha256(value.encode()).hexdigest()}
               for name, value in changes.items()}
    if args.apply:
        # Create all original backups before modifying either file.
        for name in changes:
            backup = boot / (name + ".before-denden-servo-recovery")
            if backup.exists():
                if changes[name] != original[name] and backup.read_text() != original[name]:
                    raise SystemExit("Existing backup differs; inspect before proceeding")
            else:
                with backup.open("x") as output:
                    output.write(original[name]); output.flush(); os.fsync(output.fileno())
        for name, value in changes.items():
            if value == original[name]:
                continue
            target = boot / name
            temporary = boot / (name + ".denden-recovery.tmp")
            if target.read_text() != original[name]:
                raise SystemExit("Boot file changed since preview: " + name)
            with temporary.open("x") as output:
                output.write(value); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, target)
            if target.read_text() != value:
                raise SystemExit("Readback failed: " + name)
    print(json.dumps({"boot": str(boot), "applied": args.apply, "files": summary}, indent=2))


if __name__ == "__main__":
    main()
