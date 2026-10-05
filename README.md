# DenDenMushi 🐌

A macOS desktop app that connects your prepared DenDenMushi snail’s camera,
microphone, speakers and movement controls to your Mac.

**Start here: [Install DenDenMushi on your Mac](docs/INSTALL.md).**
The guide covers desktop installation and connection to an already configured
snail. You do not need to set up Raspberry Pi services to add another Mac.

**Status:** experimental prototype. Apple Silicon builds are tested; Intel is
unverified. Builds and automated tests have passed on GitHub, but a complete
first installation and call on another user’s Mac still need validation.

## Features

- Live camera preview and OBS Virtual Camera for Meet / Telegram.
- Two-way Wi-Fi or Bluetooth audio with speaker and microphone level controls.
- Wi-Fi echo cancellation; residual echo can still occur.
- Eye open/close buttons and a camera-tilt slider for the calibrated snail.
- Saved connections, Wi-Fi network management and device history.
- A 3-minute Bluetooth pairing window after an explicit full disconnect (requires the updated Pi service).
- Waiting-sound volume and mute controls.
- English, Ukrainian and Czech interface languages.
- GitHub update checks and an explicit **Update and restart** button.

## Mac requirements

| Component | Requirement |
| --- | --- |
| Operating system | macOS 14 or later; Apple Silicon tested, Intel unverified |
| Build tools | Xcode / Command Line Tools 16+ with Swift 6+; a macOS version supporting those tools |
| Python | Python 3 for building the audio package and updates |
| Virtual camera | [OBS Studio](https://obsproject.com/download) in `/Applications/OBS.app` |
| Snail access | A powered-on, already configured device and its owner-provided connection code |
| Connection | Same local network; Mac Bluetooth enabled for initial discovery |

The project currently distributes source code, not a notarized binary installer.
Build for your Mac using the [step-by-step guide](docs/INSTALL.md).

## Install with an agent

Open the project in a local Codex or Claude Code session and ask it to install
only the Mac app using [AGENTS.md](AGENTS.md) and [the installation guide](docs/INSTALL.md).
[CLAUDE.md](CLAUDE.md) points Claude to the same instructions. The agent can check
dependencies and build; you complete macOS prompts and enter the snail’s code.

## Build from source

After installing the prerequisites:

```sh
git clone https://github.com/TimeSkipe/DenDenmushi.git
cd DenDenmushi
bash scripts/doctor.sh
```

Resolve any required missing components, then run:

```sh
python3 scripts/build-wifi-drivers.py
bash scripts/build-app.sh
open build/DenDenMushi.app
```

The build does not install audio drivers or modify the snail. Wi-Fi audio
installation is a separate Settings action that briefly restarts Mac audio;
do it outside a call. Keep only one copy of DenDenMushi running.

If the app initially opens in Ukrainian, go to **Налаштування → Мова програми**
and select **English**. See the guide for connection and call-device selection.

## Updates

Use **Settings → App updates**. Updating is available after disconnecting the
snail and builds the selected `main` commit locally. Developer tools and internet
access are required; a previous-app backup is retained. Pi services and installed
audio drivers are not automatically updated. See [Update the app](docs/INSTALL.md#update-the-app).

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
bash scripts/test-swift.sh
bash scripts/test-localization.sh
```

Run Swift checks after building the app. Python tests use fake devices,
temporary files and local sockets. GitHub Actions checks Python tests and the
macOS build. These checks do not verify real audio quality or mechanical safety.

`mac/` contains the SwiftUI app; `pi/` contains the device services;
`scripts/` contains build tools; `tests/` contains checks; `Resources/` contains
translations and artwork; `vendor/BlackHole/` contains virtual audio driver source.

Device builders and maintainers can consult [device setup notes](docs/SETUP.md)
and [hardware limits](docs/HARDWARE.md). Those are not desktop installation steps.

## License

GPL-3.0-only; see [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
The public waiting cue is synthesized by `scripts/generate-waiting-tone.py`;
no YouTube recording is included. This is an independent fan project.
