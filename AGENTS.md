# DenDenMushi: instructions for installation and coding agents

Read this file and `docs/INSTALL.md`. Read `docs/HARDWARE.md` and
`docs/SETUP.md` only for device-side work explicitly requested by the owner.
Respond in the user's language. These instructions apply only to this repository.

## When asked to install

Carry out authorized work instead of merely printing commands. By default, an
installation request means the Mac app and connection to an already prepared
snail. Do not request a Pi Linux username/password, reinstall device services,
or include SD-card or wiring tasks in desktop installation. First-time Pi setup
is a separate owner task, only when explicitly requested.

1. Run `bash scripts/doctor.sh` (read-only). Explain what is installed, missing,
   automatic, and user-assisted. macOS 14+ is required. Apple Silicon is tested;
   Intel is not. Linux/Windows cannot run this desktop app.
   Resolve MISSING/UNSUPPORTED before building. OPTIONAL SETUP audio drivers
   can wait until the app is built. Do not call PRESENT a functional sound test.
2. Check Xcode 16+ / Command Line Tools with Swift 6+, Python 3 and OBS. Install missing dependencies
   within the user's setup authorization, from official sources. Verify current
   installation guidance. Use an existing package manager if suitable; do not
   add one or execute remote shell installers without explaining the change.
   Preserve existing OBS configuration and installations.
3. If Xcode tools are missing, `xcode-select --install` starts Apple's dialog;
   explain that the user must finish it. For OBS use obsproject.com or an
   already-installed package manager, placing OBS in `/Applications/OBS.app`.
4. Build automatically in order:
   `python3 scripts/build-wifi-drivers.py`, `bash scripts/build-app.sh`.
   These commands do not install drivers or touch Pi. Do not replace an existing
   user's custom waiting sound or settings during an update.
5. Verify `codesign --verify --strict build/DenDenMushi.app`. Install in
   Applications when requested, preserving an existing copy before replacement.
   Do not replace/restart a running call without prior authorization and do not
   launch two app copies concurrently.
6. The user completes macOS privacy/admin prompts. Explain permissions when
   needed; never disable Gatekeeper, SIP or privacy controls. Use protected app
   fields for passwords, not chat/public issues or command-line arguments.
   Distinguish the Mac administrator password, Pi Linux account password,
   snail pairing code and Wi-Fi password. Public cloning needs no GitHub token.
7. Install Wi-Fi audio devices through app Settings. This restarts CoreAudio
   and interrupts all Mac sound: do it outside a call. Honor existing permission
   instead of repeatedly asking for the same authorization.
8. For a prepared snail, use Connect and its owner's device code. Do not reinstall
   Pi services as an ordinary connection step.
9. Only if the owner explicitly requests first-time Pi setup, obtain its address/username from the owner, verify its SSH host
   key and use the protected setup form. Read `pi/install.sh` before installation;
   verify OS, required commands, NetworkManager, BlueZ, PipeWire/WirePlumber,
   kernel PWM and pin conflicts. Install missing packages only on the identified
   target within granted scope using its OS repositories. Never blindly replace
   a working network/audio stack. A blank SD card needs separate OS preparation.
10. Verify launch, non-secret status and connectivity without generating sound
    or moving motors. Real audio/mechanical testing needs a suitable moment and
    user observation. Never silently record a conversation or report untested
    hardware as working.

Finish with: installed Mac components, connection completed/pending, permissions
the user must finish, call devices to select, checks completed and limitations.

| Agent work within installation scope | User/system interaction |
| --- | --- |
| Environment checks, clone, build, signature verification | Apple installation and privacy dialogs |
| Official Mac dependency downloads and authorized installation | Mac administrator prompts |
| Test environment and mock tests | Snail connection code from its owner |
| Setup guidance and non-secret status checks | Protected password/code entry and audible test results |

## Hardware constraints

- Never move/arm servos merely to test installation or confirm an unknown shaft
  position automatically. The mechanism may already be mounted.
- Preserve mapping, limits and 0.5-second timing in `docs/HARDWARE.md`.
  Physical pin numbers are not BCM GPIO numbers.
- Prototype limits are not calibration for a new user's mechanism. Do not expand
  them with a mounted linkage. Stop on pin conflicts or unsupported kernel PWM.
- Do not introduce pigpio/DMA or remove safety checks to make a test pass.

## Development and publication

- No owner usernames, real network profiles, passwords, keys, device histories,
  recovery material or private development logs in commits. Use example values.
  Do not dump credential-bearing files while debugging.
- Preserve third-party notices, source and `vendor/BlackHole/UPSTREAM_COMMIT`.
  This is a vendored snapshot, not a submodule; its revision is not this repo's
  HEAD. Build packages from source rather than committing opaque binaries.
- Public waiting audio is the original generated cue, not a YouTube recording.
- Use a virtual environment and `requirements-dev.txt`. Python checks:
  `python -m unittest discover -s tests -p 'test_*.py'`. Swift smoke checks:
  `bash scripts/test-swift.sh`, `bash scripts/test-localization.sh` after build.
  Tests use mocks/local sockets. `tests/LiveOBS.swift` is a live integration
  check; do not run it in an ongoing call without explicit authorization.
- Keep UK/EN/CS localization keys aligned.
- Rebuild the Mac app after Pi code changes: its installer embeds Pi sources.
- No notarized public binary is provided yet. Do not recommend disabling macOS
  protection as the installation process.
- Never force-push or overwrite unrelated work while installing.
