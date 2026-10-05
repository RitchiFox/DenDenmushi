#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/cache"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" "$ROOT/mac/Core.swift" "$ROOT/tests/Smoke.swift" -o "$ROOT/build/smoke"
"$ROOT/build/smoke"
xcrun swiftc -swift-version 5 -D SERVO_MODEL_TESTS -parse-as-library -module-cache-path "$ROOT/build/cache" \
  "$ROOT/mac/Core.swift" "$ROOT/mac/SetupCore.swift" "$ROOT/mac/Localization.swift" \
  "$ROOT/mac/ServoAngleScale.swift" "$ROOT/mac/Servos.swift" "$ROOT/tests/ServoInteractionSmoke.swift" \
  -o "$ROOT/build/servo-interaction-smoke"
"$ROOT/build/servo-interaction-smoke"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" \
  "$ROOT/mac/AudioLevels.swift" "$ROOT/tests/AudioLevelsSmoke.swift" -o "$ROOT/build/audio-levels-smoke"
"$ROOT/build/audio-levels-smoke"
bash "$ROOT/scripts/test-audio-admin.sh"
