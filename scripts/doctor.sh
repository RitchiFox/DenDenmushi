#!/bin/bash
# Prerequisite checks: temporary type-check only; no installation, device access or audio.
set -u
missing=0
check_toolchain() (
  # A version check alone misses incomplete compiler/SDK combinations (for
  # example, an SDK whose @State macro implementation is absent from the CLT).
  # Match build-app.sh's language/target flags and honor its SDKROOT selection.
  probe="$(mktemp -d "${TMPDIR:-/tmp}/denden-swiftui.XXXXXX")" || {
    echo 'MISSING: SwiftUI check — could not create a temporary directory'
    exit 1
  }
  trap 'rm -rf -- "$probe"' EXIT
  # Contain xcrun's lookup cache and Swift's scratch files (even --version
  # creates them). The subshell preserves the caller's TMPDIR.
  export TMPDIR="$probe"
  if ! xcrun --find swiftc >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
    echo 'MISSING: Swift/Clang — finish Xcode Command Line Tools installation'
    exit 1
  fi
  swift_major="$(xcrun swiftc --version | sed -nE 's/.*Swift version ([0-9]+).*/\1/p' | head -1)"
  if [ -z "$swift_major" ] || [ "$swift_major" -lt 6 ]; then
    echo 'MISSING: Swift 6+ — update Xcode / Command Line Tools (Xcode 16+)'
    exit 1
  fi
  echo 'OK: Swift 6+ and Clang toolchains'
  cat > "$probe/Probe.swift" <<'SWIFT' || exit 1
import SwiftUI
struct Probe: View {
    @State private var enabled = false
    var body: some View { Text(enabled ? "On" : "Off") }
}
SWIFT
  if xcrun swiftc -swift-version 5 -parse-as-library -typecheck \
    -target "$(uname -m)-apple-macosx14.0" -module-cache-path "$probe/cache" \
    "$probe/Probe.swift" > "$probe/compiler.log" 2>&1; then
    echo 'OK: SwiftUI @State type-check (selected compiler/SDK)'
    exit 0
  fi
  sed -n '1,12p' "$probe/compiler.log" >&2
  echo 'MISSING: SwiftUI compiler/SDK support — the selected toolchain failed the @State type-check.'
  echo 'Select or update compatible Xcode / Command Line Tools, then rerun doctor before building.'
  echo 'If using an explicit SDKROOT, use the same selection for doctor and the build commands.'
  exit 1
)
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
if ! check_toolchain; then
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
echo 'Next: docs/INSTALL.md; agents also read AGENTS.md.'
exit "$missing"
