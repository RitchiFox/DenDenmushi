#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/cache"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" \
  -framework CryptoKit -framework Security -framework AppKit \
  "$ROOT/mac/Localization.swift" "$ROOT/mac/Core.swift" "$ROOT/mac/SetupCore.swift" "$ROOT/mac/Bootstrap.swift" "$ROOT/tests/SetupSmoke.swift" -o "$ROOT/build/setup-smoke"
"$ROOT/build/setup-smoke" askpass "$ROOT/build/DenDenMushi.app/Contents/MacOS/DenDenMushi"
export DENDEN_SETUP_SMOKE="$ROOT/build/setup-smoke"
"${DENDEN_PYTHON:-python3}" -m unittest discover -s "$ROOT/tests" -p 'test_provision.py' -v
