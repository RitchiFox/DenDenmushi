#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"${DENDEN_PYTHON:-python3}" -m unittest discover -s "$ROOT/tests" -p test_audio.py -v
"${DENDEN_PYTHON:-python3}" -m unittest discover -s "$ROOT/tests" -p test_quiet.py -v
"${DENDEN_PYTHON:-python3}" -m unittest discover -s "$ROOT/tests" -p test_server.py -v
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" -framework CoreAudio \
  "$ROOT/mac/Core.swift" "$ROOT/mac/Audio.swift" "$ROOT/mac/AudioLevels.swift" "$ROOT/tests/AudioSmoke.swift" -o "$ROOT/build/audio-smoke"
"$ROOT/build/audio-smoke"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" -framework CoreAudio -framework IOBluetooth \
  "$ROOT/mac/Core.swift" "$ROOT/mac/Audio.swift" "$ROOT/mac/AudioRecovery.swift" "$ROOT/tests/AudioRecoverySmoke.swift" -o "$ROOT/build/audio-recovery-smoke"
"$ROOT/build/audio-recovery-smoke"
