#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SWIFT_MAJOR="$(xcrun swiftc --version | sed -nE 's/.*Swift version ([0-9]+).*/\1/p' | head -1)"
if [ -z "$SWIFT_MAJOR" ] || [ "$SWIFT_MAJOR" -lt 6 ]; then
  echo 'Swift 6+ is required. Update Xcode / Command Line Tools (Xcode 16+).' >&2
  exit 1
fi
BUILD="${DENDEN_BUILD_DIR:-$ROOT/build}"
APP="$BUILD/DenDenMushi.app"
if [ ! -f "$ROOT/Resources/DenDenMushi-WiFi-Audio.pkg" ]; then
  echo 'Build audio devices first: python3 scripts/build-wifi-drivers.py' >&2
  exit 1
fi
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$BUILD/cache"
xcrun swiftc -swift-version 5 -parse-as-library -O \
  -target "$(uname -m)-apple-macosx14.0" -module-cache-path "$BUILD/cache" \
  -framework AVFoundation -framework SwiftUI -framework AppKit -framework Network -framework CoreAudio -framework CryptoKit -framework CoreBluetooth -framework Security -framework IOBluetooth -framework CoreWLAN -framework CoreLocation \
  "$ROOT"/mac/*.swift \
  -o "$APP/Contents/MacOS/DenDenMushi"
cp -R "$ROOT/pi" "$ROOT/scripts" "$APP/Contents/Resources/"
cp "$ROOT/README.md" "$APP/Contents/Resources/README.md"
cp "$ROOT/LICENSE" "$ROOT/THIRD_PARTY_NOTICES.md" "$APP/Contents/Resources/"
cp -R "$ROOT/docs" "$APP/Contents/Resources/"
find "$APP/Contents/Resources/pi" -type d -name __pycache__ -prune -exec rm -rf {} +
cp -R "$ROOT/Resources/". "$APP/Contents/Resources/"
SOURCE_COMMIT="$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null || echo development)"
printf '%s\n' "$SOURCE_COMMIT" > "$APP/Contents/Resources/SourceCommit.txt"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>DenDenMushi</string>
<key>CFBundleIdentifier</key><string>dev.denden.demo.local</string>
<key>CFBundleName</key><string>DenDenMushi</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.9.2</string>
<key>CFBundleVersion</key><string>60</string>
<key>CFBundleDevelopmentRegion</key><string>uk</string>
<key>CFBundleLocalizations</key><array><string>uk</string><string>cs</string><string>en</string></array>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSLocationUsageDescription</key><string>Щоб визначати назву Wi-Fi цього Mac і підключати равлика до тієї самої мережі.</string>
<key>NSLocationWhenInUseUsageDescription</key><string>Щоб визначати назву Wi-Fi цього Mac і підключати равлика до тієї самої мережі.</string>
<key>NSMicrophoneUsageDescription</key><string>Щоб передавати звук із віртуального пристрою DenDenMushi до динаміків равлика через Wi-Fi.</string>
<key>NSLocalNetworkUsageDescription</key><string>Щоб знайти й підключити твій DenDenMushi у локальній мережі.</string>
<key>NSBluetoothAlwaysUsageDescription</key><string>Щоб знайти DenDenMushi, передати налаштування Wi-Fi та відновити його Bluetooth-звук.</string>
<key>NSBonjourServices</key><array><string>_denden._tcp</string></array>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
# No system extension in this app. OBS supplies its signed camera extension.
# A local ad-hoc signature is sufficient; an available identity can be passed explicitly.
codesign --force --sign "${DENDEN_SIGN_IDENTITY:--}" "$APP"
codesign --verify --strict "$APP"
echo "Built: $APP"
