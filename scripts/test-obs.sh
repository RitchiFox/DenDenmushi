#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/cache"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" -framework CoreAudio "$ROOT/mac/Core.swift" "$ROOT/mac/Audio.swift" "$ROOT/tests/OBSSmoke.swift" -o "$ROOT/build/obs-smoke"
python3 "$ROOT/tests/obs_mock.py" "$ROOT/build/obs-smoke"
