#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m unittest discover -s "$ROOT/tests" -p 'test_localization.py' -v
mkdir -p "$ROOT/build/cache"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" \
  "$ROOT/mac/Localization.swift" "$ROOT/tests/LocalizationSmoke.swift" -o "$ROOT/build/localization-smoke"
"$ROOT/build/localization-smoke" "$ROOT/build/DenDenMushi.app"
