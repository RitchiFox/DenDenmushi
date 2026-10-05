# Install DenDenMushi on your Mac

This guide installs the desktop app and connects it to an **already configured
DenDenMushi snail**. You only need to set up your Mac. Ask the device owner for
the snail’s connection code and which Wi-Fi network to use.

## What you need

- A Mac running macOS 14 or later. Apple Silicon is tested; Intel is unverified.
- Xcode / Command Line Tools 16 or later with **Swift 6+** for the local build.
  Your macOS version must also support the developer tools you install.
- Python 3 and an internet connection for building and updating the app.
- [OBS Studio](https://obsproject.com/download) in `/Applications/OBS.app`.
  OBS provides the virtual camera used by video-call apps.
- A powered-on, prepared snail and access to the same local network.

The app currently ships as source code. With the recommended agent-assisted
installation below, the agent builds it for you and installs a normal app in
Applications. You launch it by clicking its icon; everyday use does not need
Terminal. There is no ready-made notarized installer yet. The Mac app and audio package have built successfully locally
and on GitHub’s macOS runner. A complete first installation and call on another
user’s Mac have not yet been verified.

## Option A: let Codex or Claude Code help

Open this repository folder in your local coding agent and send this request:

> Install the DenDenMushi desktop app on my Mac using AGENTS.md and
> docs/INSTALL.md. The snail is already configured. Check existing dependencies,
> install missing Mac components within the setup request, build the app, and
> install DenDenMushi.app in Applications. Open and verify the installed copy.
> Complete the Wi-Fi audio setup through its native installer so both microphone
> and speakers appear. Perform the build and installation work yourself; do not
> ask me to type Terminal commands. Explain which macOS prompts I need to
> complete. Do not reinstall Raspberry Pi services, move servos, or run
> audio tests without my request.

If you do not have the files yet, ask the agent to clone
`https://github.com/TimeSkipe/DenDenmushi.git` into a new folder first.
Codex uses `AGENTS.md`; Claude Code uses `CLAUDE.md`, which points to the same
instructions. The agent needs terminal access on your Mac. A chat without
local tools can guide you but cannot install the app for you.

When finished, open **Finder → Applications → DenDenMushi**, or search for
DenDenMushi in Spotlight. If the agent used your personal Applications folder,
it will give you that location. The repository and Terminal can remain closed.

| The agent can do | You need to do |
| --- | --- |
| Check macOS, developer tools, Python and OBS | Complete Apple installer and permission dialogs |
| Download the source and build the app and audio package | Approve the Mac audio installer when needed |
| Install authorized Mac dependencies and check the build | Enter the snail’s code in the app |
| Check connection status and explain errors | Select call devices and confirm sound quality when convenient |

## Option B: manual source build (optional, for experienced users)

Skip this section when an agent is installing the app for you. It describes
developer commands, not steps every user needs to perform.

### 1. Prepare your Mac

1. Check your macOS version in **Apple menu → About This Mac**.
2. Download OBS Studio and move it into **Applications**. Allow its camera
   extension if macOS asks. Preserve an existing OBS installation.
3. Open **Terminal** using Spotlight: press `Command–Space`, type `Terminal`,
   and press Return. Run:

   ```sh
   xcode-select --install
   ```

   Complete Apple’s installer dialog. If tools are already installed, check:

   ```sh
   xcrun swiftc --version
   ```

   You need Swift 6 or later. If it reports Swift 5, update Xcode / Command
   Line Tools before continuing. Installing tools may require a macOS update.
4. Check Python:

   ```sh
   python3 --version
   ```

   If Python 3 is missing, install it from
   [python.org](https://www.python.org/downloads/macos/).

### 2. Download and check the project

Run each command block in the same Terminal window. Do not copy the Markdown
backticks around the blocks.

```sh
git clone https://github.com/TimeSkipe/DenDenmushi.git
cd DenDenmushi
bash scripts/doctor.sh
```

If the folder already exists, do not delete it: it may be your previous copy.
Use that checkout or choose another empty folder.

Read the check results before continuing:

- **OK**: the required component was found.
- **MISSING / UNSUPPORTED**: fix the reported issue and repeat the check.
- **OPTIONAL SETUP** for audio drivers: expected before first installation;
  you install them later from the app’s settings.
- **PRESENT**: the driver files exist; this does not verify sound quality.

### 3. Build and open the app

Once no required components are missing, run:

```sh
python3 scripts/build-wifi-drivers.py
bash scripts/build-app.sh
```

The build can take several minutes. On success, the last step prints `Built:`
followed by the app’s location. If the build fails, stop and share the error
with your agent instead of trying to open a partial build.

```sh
open build/DenDenMushi.app
```

These commands build the app and audio installer. They do not install audio
drivers or change the snail. You do not need a GitHub account or token.

To keep the app in Applications, first choose **Quit DenDenMushi**, then move
`build/DenDenMushi.app` there in Finder and open the moved copy. Do not run two
copies at once. Leave macOS security protections enabled.

### 4. App language

New installations start in English. Existing language preferences are kept.
If an older installation opens in Ukrainian, select the **Налаштування** tab (Settings),
then **Мова програми** (App language), and choose **English**. Labels below
refer to the English interface. The language choice is remembered on this Mac.

### 5. Connect to your prepared snail

1. Turn on the snail and enable Bluetooth on your Mac for the initial search.
2. Connect your Mac to the local network provided by the device owner.
3. Click **Connect snail**.
4. When prompted, enter the **snail’s password or code** supplied by its owner.
   This is not the Mac login password or a Raspberry Pi Linux account password.
5. Complete macOS Bluetooth and local-network permission prompts if shown.
6. Wait for the live camera preview.

Do not use **Prepare a new Pi / update the service** for an ordinary first
connection from another Mac. If the device itself needs preparation or an
update, contact its owner.

If DenDenMushi asks you to close OBS, quit OBS using its menu and click
**Connect snail** again. DenDenMushi can then configure and launch OBS.
Do this outside an existing OBS recording or broadcast.

### Optional: pair in macOS Bluetooth first

With the updated app and device service, the current user can click
**Disconnect completely** to make the snail visible for **3 minutes**.
On the other Mac, open **System Settings → Bluetooth**, select **DenDenMushi**
and connect. Then open the app, click **Connect snail** and enter the owner’s
snail code. Both Macs still need access to the snail’s local network for video
and Wi-Fi audio.

The pairing window closes after one new device pairs, when the app starts a
camera connection, or when the timer expires. Pairing does not replace the
app’s code-based access. Normal discovery through **Connect snail** remains
available without this extra system-pairing step. If the timer has expired,
the owner can connect and then fully disconnect again to reopen it.

If the app reports that pairing mode is unavailable, the owner needs to update
the Pi service separately; updating only the Mac app does not add it to an older
snail. Do not reinstall device services just to join as a guest.

### 6. Set up Wi-Fi audio and your call

1. Open **Settings → Audio transport** and select **Wi-Fi**. This is the default
   for a new installation; existing transport preferences are kept.
2. If offered, click **Install Wi-Fi audio devices on Mac**. macOS may request
   your **Mac administrator password**. This restarts system audio and briefly
   interrupts other sound on your Mac, so install outside a call.
3. Complete the microphone permission prompt if macOS asks. This permission
   is also used to read the virtual speaker device for Wi-Fi playback.
4. In Google Meet or Telegram, choose:

   | Setting | Device |
   | --- | --- |
   | Camera | **OBS Virtual Camera** |
   | Microphone | **DenDenMushi Wi-Fi Microphone** |
   | Speakers | **DenDenMushi Wi-Fi Speakers** |

5. When it is convenient to make sound, try a short call. Residual echo is still
   possible. The device owner can adjust speaker volume and microphone gain
   under **Settings → Superadmin audio settings**, after connecting and entering
   their separate superadmin code. Guests do not need this code for normal use.

The speaker and microphone pickers apply your choice immediately; there is no
separate Apply button. These pickers change the Mac's default audio devices.
Your call app may keep its own explicit device selection, so check the device
lists above in Meet or Telegram too.

The superadmin code is configured privately on the snail. It is not included
in this repository and is separate from the connection code. Access expires
after ten minutes or when this Mac disconnects; gain controls stay hidden until
unlocked again.

An optional permission to determine the Mac’s Wi-Fi network may be requested
for network-following features. It is separate from the microphone permission.

## How to tell whether setup succeeded

- The app opens without a launch error.
- **Connect snail** produces a live view from the snail’s camera.
- Selecting **OBS Virtual Camera** in your call shows that camera.
- Both DenDenMushi Wi-Fi audio devices appear in the call’s device lists.
- In a suitable test call, the other person hears the handset microphone and
  you hear them through the snail’s speakers.

A successful build or a visible device name alone does not confirm a working
call. Eye and camera movement controls are for the already calibrated device;
no mechanical calibration is required just to install the app on another Mac.

## Update the app

The app can check the repository’s `main` branch at launch. This only checks
for a new version; it does not silently install code. Turn it off with
**Settings → App updates → Check GitHub at launch** if preferred.

1. Finish your call and click **Disconnect completely**.
2. Open **Settings → App updates → Check for updates**.
3. If a new version is available, click **Update and restart**.
4. The app closes. Terminal downloads the selected commit, builds it and
   reopens the app. Leave Terminal open until it finishes. Internet access,
   Python 3 and the developer tools are still required.
5. The previous copy stays beside it as `DenDenMushi-backup-….app`.

A download or build failure preserves the current app. Updating the desktop
app does not install Pi services or update the installed Mac audio drivers.
macOS may ask for permissions again after the locally signed app changes.

The app’s containing folder must be writable. If necessary, use your own
`~/Applications` folder. To return to a backup, quit the current app and open
the backup copy. Updates from `main` are prototype updates, not a stable-release
channel. Keep custom code changes in a fork and update those builds manually.
The full live self-update process has not yet been tested on another user’s Mac.

## Troubleshooting

| Problem | Check |
| --- | --- |
| `xcrun` or `swiftc` is missing | Finish installing Xcode Command Line Tools |
| Swift 5 is reported | Update to tools providing Swift 6+ |
| Build says the audio package is missing | Run `python3 scripts/build-wifi-drivers.py` first |
| OBS Virtual Camera is missing | OBS is in Applications and its camera extension is allowed |
| App asks you to close OBS | Quit OBS outside a recording/broadcast, then reconnect |
| Snail is not found | Power, Mac Bluetooth, the owner’s network and macOS permissions |
| Snail code is rejected | Ask its owner for the current code; do not substitute your Mac password |
| Wi-Fi audio devices are missing | Install them from Settings, then reopen the call’s device list |
| Echo remains | Select both Wi-Fi devices; lower excessive volume/gain and separate mic from speakers |

If you need help, include your macOS version, the failing step and the error
text. Never share passwords, device access codes or SSH keys in an issue.
