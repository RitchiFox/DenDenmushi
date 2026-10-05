# Setup and current limitations

## Raspberry Pi

Use an existing SSH-enabled Pi account with sudo access. Create its credentials
yourself; this repository contains no preconfigured access keys or passwords.
The tested audio stack was PipeWire 1.2.7 with WirePlumber 0.4.13. A different
WirePlumber major version may need configuration changes. The kernel must
provide `pwm-gpio` and `/boot/firmware/overlays/pwm-gpio.dtbo`.

The installer checks for `rpicam-vid`, `ffmpeg`, `parec`, `pacat`, `paplay`
and Python. It may install Python D-Bus, GI and cryptography packages plus
Raspberry Pi Device Tree utilities. NetworkManager and Bluetooth must already
work in the OS. This is not a blank-SD-card imaging tool.

Owner preparation installs files in `/opt/denden-demo`, creates systemd
services, configures GPIO permissions, Bluetooth/audio and provisioning.
Review `pi/install.sh` for the complete system changes. The app requests
confirmation of a new SSH host key before sending account credentials.
Passwords are not intended to be saved; subsequent operation uses provisioned
access. Do not expose the Pi's control services directly to the Internet.

## Wi-Fi and sound

Pi 3 Model B supports 2.4 GHz Wi-Fi. A 5 GHz-only SSID cannot be used by this
board. Mac may use another band if both devices can communicate on the same
LAN. Guest isolation, captive portals and enterprise authentication are not
supported by the Wi-Fi setup form.

The saved-network list works over the active SSH connection. Automatic
cross-network following has limited hardware validation; BLE-only recovery
has occasionally timed out. Keep an ordinary SSH recovery path available.

For Wi-Fi audio, build and install the bundled virtual device package once.
It restarts CoreAudio and briefly interrupts other Mac audio. Select both
DenDenMushi Wi-Fi devices in the call application. Start with modest speaker
volume and microphone gain; high gain can distort sound and worsen echo.

WebRTC echo cancellation runs on Pi with paired playback/capture routes.
A 10ms processing cadence reduced observed xruns and remote echo on the
prototype. Echo elimination is not guaranteed; the persistent node setting
still needs confirmation after a fresh service restart on the prototype.
If filter initialization fails, audio falls back to the raw endpoints; the
Pi status reports the AEC error. Do not assume that connected audio means
echo cancellation is active. Hardware placement remains important.

Bluetooth full-duplex audio is experimental and has telephone-quality limits.
Wi-Fi audio can also drop out under network stalls. Preview refresh and call
video rates differ; OBS supplies the video device used by the call.

## Owner-only audio settings

Guest Macs do not need the audio superadmin code. Normal microphone/speaker
selection and connection remain available without it. Speaker gain, microphone
gain and their hardware mute controls require a ten-minute admin session.
Disconnecting or locking Settings revokes that Mac's session.

The owner configures a separate code on the Pi using the protected prompt:

```sh
sudo python3 /opt/denden-demo/audio_admin.py configure --owner YOUR_PI_SERVICE_USER
```

Replace the account placeholder with the existing service account. The tool
asks for the new code twice without displaying it. Do not put a real code in
command arguments, Git, screenshots or issue reports. The installer has no
default code and preserves the private device file during updates. Without a
configured code the gain controls remain locked; other audio features still work.

## Distribution

Build locally for your Mac architecture. Neither the app nor the generated
audio installer is notarized. This repository does not instruct users to
disable Gatekeeper or other system security. A signed, notarized release is
a separate future distribution step.

## Reporting issues

Include macOS version, Pi board/OS, audio mode and steps to reproduce.
Remove Wi-Fi passwords, access codes, SSH keys, identifying hostnames and
other private information before sharing logs. Python mock tests do not
replace a hardware test of camera, audio or motion.
