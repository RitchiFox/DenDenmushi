#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/cache"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" \
  "$ROOT/mac/AudioAdmin.swift" "$ROOT/tests/AudioAdminSmoke.swift" -o "$ROOT/build/audio-admin-smoke"
"$ROOT/build/audio-admin-smoke"
