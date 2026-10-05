#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ "$(id -u)" -ne 0 ] || [ -z "${SUDO_USER:-}" ]; then
  echo 'Run with sudo ./install.sh from the Pi account used for DenDenMushi.' >&2
  exit 1
fi
DENDEN_USER="$SUDO_USER"
DENDEN_UID="$(id -u "$DENDEN_USER")"
DENDEN_SPEAKER_OUTPUT="${DENDEN_SPEAKER_OUTPUT:-usb}"
case "$DENDEN_SPEAKER_OUTPUT" in headphones|usb) ;; *) echo 'Invalid speaker output.' >&2; exit 1 ;; esac
# A failed first hardware start must not be retried automatically on boot.
# Only re-enable servos after their live readiness check succeeds below.
systemctl disable --now denden-servos.service 2>/dev/null || true
systemctl disable --now denden-motion.service 2>/dev/null || true
systemctl stop denden-pwm-setup.service 2>/dev/null || true
command -v rpicam-vid >/dev/null
command -v ffmpeg >/dev/null
command -v parec >/dev/null
command -v pacat >/dev/null
command -v paplay >/dev/null
command -v python3 >/dev/null
# The stock Bookworm image usually already contains all three packages.
if ! /usr/bin/python3 -c 'import dbus; import gi; from cryptography.hazmat.primitives.ciphers.aead import AESGCM' >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-dbus python3-gi python3-cryptography
fi
if [ ! -x /usr/bin/dtoverlay ]; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y raspi-utils-dt
fi
/sbin/modinfo pwm-gpio >/dev/null
test -f /boot/firmware/overlays/pwm-gpio.dtbo
# Stop the managed call before installing its root-only startup helper.
# Starting the service later will apply the verified SCO route before HTTP opens.
if systemctl cat denden-demo.service >/dev/null 2>&1; then
  systemctl stop denden-demo.service
fi
install -d -o root -g root -m 755 /opt/denden-demo
/usr/bin/python3 -E -s -B install_files.py
install -o root -g root -m 644 waiting.wav /opt/denden-demo/waiting.wav
# Retire the old root/DMA service. The boot recovery mask stays in place.
if [ -f /etc/systemd/system/denden-servos.service ] && [ ! -L /etc/systemd/system/denden-servos.service ]; then
  cp -p /etc/systemd/system/denden-servos.service /etc/systemd/system/denden-servos.service.retired
  rm /etc/systemd/system/denden-servos.service
fi
systemctl mask denden-servos.service
if systemctl is-active --quiet pigpiod.service; then
  echo 'pigpiod is already running; stop the other GPIO application before installing servos.' >&2
  exit 1
fi
usermod -a -G gpio "$DENDEN_USER"
cat > /etc/systemd/system/denden-pwm-setup.service <<EOF
[Unit]
Description=Prepare three disabled DenDenMushi kernel PWM outputs
After=local-fs.target
Before=denden-motion.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 -E -s -B /opt/denden-demo/servo_pwm_setup.py prepare $DENDEN_USER
ExecStop=/usr/bin/python3 -E -s -B /opt/denden-demo/servo_pwm.py stop
TimeoutStartSec=60
TimeoutStopSec=3
NoNewPrivileges=true
ProtectHome=true
PrivateTmp=true
EOF
cat > /etc/systemd/system/denden-motion.service <<EOF
[Unit]
Description=DenDenMushi three SG92R servos (kernel PWM, private socket)
Requires=denden-pwm-setup.service
After=local-fs.target denden-pwm-setup.service

[Service]
Type=notify
NotifyAccess=main
User=$DENDEN_USER
Group=$(id -gn "$DENDEN_USER")
SupplementaryGroups=gpio
WorkingDirectory=/run/denden-servos
ExecStart=/usr/bin/python3 -E -s -B /opt/denden-demo/servos.py
Environment=DENDEN_SERVO_DRIVER=kernel-pwm
# Kernel PWM survives a broker crash, so PID1 independently disables the exact
# three outputs after exit or a missed watchdog. Never renew in another thread.
ExecStopPost=+/usr/bin/python3 -E -s -B /opt/denden-demo/servo_pwm.py stop
WatchdogSec=1
WatchdogSignal=SIGKILL
TimeoutStartSec=10
RuntimeDirectory=denden-servos
RuntimeDirectoryMode=0750
UMask=0007
Restart=no
TimeoutStopSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
RestrictAddressFamilies=AF_UNIX
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
EOF
# Read the onboard Bluetooth voice routing while the owner has authorized
# installation. This diagnostic never changes radio power, address or routing.
# An unsupported controller/failed read must not block the normal update.
if ! /usr/bin/python3 /opt/denden-demo/bluetooth_diagnostics.py > /opt/denden-demo/bluetooth-sco-diagnostic.json; then
  printf '%s\n' '{"outcome":"inconclusive","reason":"probe_failed"}' > /opt/denden-demo/bluetooth-sco-diagnostic.json
fi
chmod 644 /opt/denden-demo/bluetooth-sco-diagnostic.json
/usr/bin/python3 /opt/denden-demo/provision_admin.py "$DENDEN_USER"
usermod -a -G video,audio,render "$DENDEN_USER"
cat > /etc/udev/rules.d/80-denden-dma.rules <<'RULE'
SUBSYSTEM=="dma_heap", GROUP="video", MODE="0660"
RULE
udevadm control --reload-rules
udevadm trigger --subsystem-match=dma_heap
if [ -d /dev/dma_heap ]; then
  find /dev/dma_heap -type c -exec chgrp video {} + -exec chmod 660 {} +
fi
# Keep the owner's sound session available without an interactive desktop login.
if ! command -v pactl >/dev/null; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y pulseaudio-utils
fi
loginctl enable-linger "$DENDEN_USER"
systemctl start "user@$DENDEN_UID.service"
runuser -u "$DENDEN_USER" -- env XDG_RUNTIME_DIR="/run/user/$DENDEN_UID" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$DENDEN_UID/bus" systemctl --user start pipewire pipewire-pulse wireplumber
cat > /etc/systemd/system/denden-demo.service <<EOF
[Unit]
Description=DenDenMushi demo camera (SSH access only)
After=network.target bluetooth.service user@$DENDEN_UID.service
Wants=bluetooth.service user@$DENDEN_UID.service

[Service]
Type=simple
User=$DENDEN_USER
Environment=XDG_RUNTIME_DIR=/run/user/$DENDEN_UID
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$DENDEN_UID/bus
Environment=DENDEN_SPEAKER_OUTPUT=$DENDEN_SPEAKER_OUTPUT
SupplementaryGroups=video audio render
# Only this fixed, root-owned helper runs as root. The HTTP/audio process keeps
# User= above. It waits for controller readiness and never resets radio power.
ExecStartPre=-+/usr/bin/python3 -E -s -B /opt/denden-demo/bluetooth_sco.py --prestart
ExecStart=/usr/bin/python3 /opt/denden-demo/server.py
Restart=on-failure
RestartSec=3
TimeoutStartSec=75
TimeoutStopSec=12
NoNewPrivileges=true
ProtectSystem=strict
# Hide home directories, exposing only the owner's audio/session sockets.
# ProtectHome=true also hides /run/user and breaks pactl from this service.
ProtectHome=tmpfs
BindReadOnlyPaths=/run/user/$DENDEN_UID
PrivateTmp=true
StateDirectory=denden-audio
StateDirectoryMode=0700
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
if command -v avahi-daemon >/dev/null; then
  install -d /etc/avahi/services
  cat > /etc/avahi/services/denden-demo.service <<'EOF'
<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group><name replace-wildcards="yes">DenDenMushi on %h</name>
<service><type>_denden._tcp</type><port>22</port>
<txt-record>version=1</txt-record><txt-record>transport=ssh</txt-record>
</service></service-group>
EOF
fi
systemctl daemon-reload
systemctl enable denden-demo.service
systemctl restart denden-demo.service
# Verify the service can reach the owner's audio session before reporting a
# successful update. This catches namespace/permission mistakes immediately.
/usr/bin/python3 - <<'PY'
import json, time, urllib.request
for attempt in range(10):
    try:
        request = urllib.request.Request('http://127.0.0.1:8789/audio/levels', headers={'X-DenDen-Client':'desktop-v1'})
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.load(response)
        output = result.get('output', {})
        if result.get('ok') and (output.get('available') or output.get('error') == 'usb_output_missing'):
            print('Audio service verified.' if output.get('available') else 'Audio service verified; USB sound card is disconnected.')
            break
    except (OSError, ValueError):
        pass
    time.sleep(0.5)
else:
    raise SystemExit('Pi audio service did not become available. Check denden-demo and the owner PipeWire session.')
PY
cat > /etc/systemd/system/denden-setup.service <<'EOF'
[Unit]
Description=DenDenMushi Bluetooth setup
Requires=bluetooth.service
After=bluetooth.service NetworkManager.service
StartLimitIntervalSec=0

[Service]
ExecStart=/usr/bin/python3 /opt/denden-demo/provision.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now denden-setup.service
systemctl restart denden-setup.service
# Camera, audio and Bluetooth are restored before trying the new GPIO broker.
# Persist generated unit files before enabling the motion service on reboot.
sync
systemctl restart denden-motion.service
/usr/bin/python3 - <<'PY'
import json, time, urllib.request
for attempt in range(20):
    request = urllib.request.Request('http://127.0.0.1:8789/servos/status', headers={'X-DenDen-Client':'desktop-v1'})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            result = json.load(response)
        if result.get('ok'):
            break
    except (OSError, ValueError):
        pass
    time.sleep(0.25)
else:
    raise SystemExit('Servo service did not become ready. Camera and Bluetooth remain available; check denden-motion.service.')
# Read only: installation must never move the attached servos.
request = urllib.request.Request('http://127.0.0.1:8789/servos/status', headers={'X-DenDen-Client':'desktop-v1'})
with urllib.request.urlopen(request, timeout=3) as response:
    result = json.load(response)
if (not result.get('ok') or result.get('driver') != 'kernel-pwm'
        or len(result.get('channels', [])) != 3 or any(c['active'] for c in result['channels'])):
    raise SystemExit('Servo service did not become ready with all outputs inactive. Check denden-motion.service.')
print('Three servo controls verified; no movement requested.')
PY
# Exercise PID1's independent shutdown while every output is still disabled.
# The camera/Bluetooth units do not depend on this broker and remain available.
/usr/bin/python3 - <<'PY'
import json, subprocess, time, urllib.request
unit = 'denden-motion.service'
def systemctl(*args):
    return subprocess.run(['/bin/systemctl', *args], text=True, capture_output=True,
                          timeout=4, check=True).stdout.strip()
passed = False
try:
    pid = systemctl('show', unit, '--property=MainPID', '--value')
    if not pid.isdigit() or int(pid) <= 0:
        raise RuntimeError('Servo broker has no running main process.')
    systemctl('kill', '--signal=SIGSTOP', '--kill-who=main', unit)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        values = dict(line.split('=', 1) for line in systemctl(
            'show', unit, '--property=MainPID,Result,ExecStopPost').splitlines() if '=' in line)
        cleanup = values.get('ExecStopPost', '')
        if (values.get('MainPID') == '0' and values.get('Result') == 'watchdog'
                and 'code=exited' in cleanup and 'status=0' in cleanup):
            from servo_pwm import KernelPwmDriver
            check = KernelPwmDriver()
            check.close()
            passed = True
            break
        time.sleep(.1)
    if not passed:
        raise RuntimeError('Independent servo shutdown did not verify; autostart stays disabled.')
finally:
    if not passed:
        systemctl('stop', unit)
systemctl('reset-failed', unit)
systemctl('start', unit)
request = urllib.request.Request('http://127.0.0.1:8789/servos/status', headers={'X-DenDen-Client':'desktop-v1'})
with urllib.request.urlopen(request, timeout=3) as response:
    result = json.load(response)
if (not result.get('ok') or result.get('driver') != 'kernel-pwm'
        or len(result.get('channels', [])) != 3 or any(c['active'] for c in result['channels'])):
    systemctl('stop', unit)
    raise SystemExit('Servo broker did not restart with all outputs disabled.')
print('Independent watchdog and stop helper verified without movement; broker restarted.')
PY
systemctl enable denden-motion.service
sync
echo 'Installed. Camera stays off until the Mac app starts it.'
echo 'Stop any previous manual rpicam-vid test before connecting the app.'
echo 'Existing Bluetooth audio profiles are preserved.'
if [ -f public_key ]; then
  /usr/bin/python3 /opt/denden-demo/provision_admin.py "$DENDEN_USER" "$PWD/public_key"
fi
