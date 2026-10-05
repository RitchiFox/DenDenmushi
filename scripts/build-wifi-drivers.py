#!/usr/bin/env python3
"""Build isolated BlackHole-derived loopbacks; retain GPL source and license."""
from pathlib import Path
import plistlib, shutil, subprocess, uuid, os
root = Path(__file__).resolve().parents[1]
vendor = root / 'vendor/BlackHole'
out = root / 'build-wifi-drivers'
stage = out / 'root/Library/Audio/Plug-Ins/HAL'
stage.mkdir(parents=True, exist_ok=True)
for role, label in [('Mic', 'Microphone'), ('Speaker', 'Speakers')]:
    name = 'DenDenWiFi' + role
    bundle = 'dev.denden.audio.' + role.lower()
    dest = stage / (name + '.driver')
    (dest / 'Contents/MacOS').mkdir(parents=True, exist_ok=True)
    (dest / 'Contents/Resources').mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'clang', '-bundle', '-O2', '-mmacosx-version-min=14.0',
        '-framework', 'CoreAudio', '-framework', 'CoreFoundation', '-framework', 'Accelerate',
        '-DkDriver_Name="' + name + '"', '-DkPlugIn_BundleID="' + bundle + '"',
        '-DkDevice_Name="DenDenMushi Wi-Fi ' + label + '"', '-DkHas_Driver_Name_Format=0',
        '-DkNumber_Of_Channels=2', '-DkSampleRates=48000',
        str(vendor / 'BlackHole/BlackHole.c'), '-o', str(dest / 'Contents/MacOS' / name)], check=True)
    factory = str(uuid.uuid5(uuid.NAMESPACE_DNS, bundle))
    data = {'CFBundleExecutable': name, 'CFBundleIdentifier': bundle, 'CFBundleName': name,
        'CFBundlePackageType': 'BNDL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
        'CFPlugInFactories': {factory: 'BlackHole_Create'},
        'CFPlugInTypes': {'443ABAB8-E7B3-491A-B985-BEB9187030DB': [factory]}}
    (dest / 'Contents/Info.plist').write_bytes(plistlib.dumps(data))
    shutil.copy(vendor / 'LICENSE', dest / 'Contents/Resources/LICENSE-BlackHole.txt')
    shutil.copy(vendor / 'BlackHole/BlackHole.icns', dest / 'Contents/Resources/BlackHole.icns')
    subprocess.run(['codesign', '--force', '--sign', '-', str(dest)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(dest)], check=True)
scripts = out / 'scripts'; scripts.mkdir(exist_ok=True)
post = scripts / 'postinstall'
post.write_text('#!/bin/sh\n# Reload only after the user explicitly installs this audio package.\n/usr/bin/killall coreaudiod 2>/dev/null || true\nexit 0\n')
post.chmod(0o755)
pkg = root / 'Resources/DenDenMushi-WiFi-Audio.pkg'
subprocess.run(['pkgbuild', '--root', str(out / 'root'), '--scripts', str(scripts),
    '--identifier', 'dev.denden.audio.wifi', '--version', '1.0', str(pkg)], check=True)
commit = (vendor / 'UPSTREAM_COMMIT').read_text().strip()
(root / 'Resources/WiFi-Audio-LICENSE.txt').write_text('These separate audio drivers are built from BlackHole, GPL-3.0.\nUpstream: https://github.com/ExistentialAudio/BlackHole\nCommit: ' + commit + '\nBuild: scripts/build-wifi-drivers.py; source: vendor/BlackHole.\n\n' + (vendor / 'LICENSE').read_text())
print(pkg)
