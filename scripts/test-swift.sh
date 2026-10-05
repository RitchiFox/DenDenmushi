#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/cache"
xcrun swiftc -swift-version 5 -parse-as-library -module-cache-path "$ROOT/build/cache" "$ROOT/mac/Core.swift" "$ROOT/tests/Smoke.swift" -o "$ROOT/build/smoke"
"$ROOT/build/smoke"
