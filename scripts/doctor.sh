#!/bin/bash
# Read-only prerequisite inventory: no installation, device access or audio.
set -u
missing=0
check() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "OK: $1"
  else
    echo "MISSING: $1 — $2"
    missing=1
  fi
}
if [ "$(uname -s)" != Darwin ]; then
  echo 'UNSUPPORTED: desktop requires macOS 14+. Python tests can run elsewhere.'
  exit 1
fi
version="$(sw_vers -productVersion)"
echo "macOS: $version; architecture: $(uname -m)"
if [ "${version%%.*}" -lt 14 ]; then
  echo 'UNSUPPORTED: macOS 14 or later required.'
  missing=1
fi
check git 'install Xcode Command Line Tools'
check xcrun 'run xcode-select --install and finish the Apple dialog'
check python3 'install Python 3 from python.org'
if xcrun --find swiftc >/dev/null 2>&1 && xcrun --find clang >/dev/null 2>&1; then
  echo 'OK: Swift and Clang toolchains'
else
  echo 'MISSING: Swift/Clang — finish Xcode Command Line Tools installation'
  missing=1
fi
if [ -d /Applications/OBS.app ]; then
  echo 'OK: OBS in /Applications'
else
  echo 'MISSING: OBS Studio in /Applications — https://obsproject.com/download'
  missing=1
fi
for role in Mic Speaker; do
  if [ -d "/Library/Audio/Plug-Ins/HAL/DenDenWiFi${role}.driver" ]; then
    echo "PRESENT: Wi-Fi $role driver (runtime not tested)"
  else
    echo "OPTIONAL SETUP: Wi-Fi $role driver — install in app Settings outside a call"
  fi
done
echo 'Pi, permissions, camera, audio quality and servo calibration were not tested.'
echo 'Next: docs/INSTALL.uk.md; agents also read AGENTS.md.'
exit "$missing"
