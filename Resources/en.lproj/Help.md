New installations use English and Wi-Fi audio. In Settings → Audio transport you can switch to Bluetooth; saved preferences are preserved. For Wi-Fi, install the Mac audio devices once using the Settings button and complete the native installer. Guests connecting to a prepared snail do not reinstall Pi services.

# DenDenMushi

## Connect

1. Turn on your snail and enable Bluetooth on your Mac. Both devices need access to the same Wi-Fi network.
2. Open DenDenMushi and click **Connect**. The app finds the snail over Bluetooth, then sends camera video over Wi-Fi.
3. On a new Mac, enter the **snail’s password or code** once. This is not your Mac password or your Raspberry Pi account password. Saved access is used automatically next time.
4. In Google Meet or Telegram, select **OBS Virtual Camera**. OBS runs in the background and needs to be installed in Applications.

The app displays a live preview up to 15 fps. The call receives the full 720p / 30 fps video stream.

## Audio

Camera and audio share the main screen. Connect starts the Wi-Fi audio session when its drivers are installed. In your call app, select **DenDenMushi Wi-Fi Microphone** and **DenDenMushi Wi-Fi Speakers**. The app’s speaker and microphone pickers apply your choice immediately to the Mac defaults; a call app may have its own device selection. If audio fails, use the audio retry button.

Speaker volume and microphone gain are available under **Settings → Superadmin audio settings**, after connecting and entering the owner’s separate superadmin code. Access expires after ten minutes or on disconnect. Release a slider to apply its value. Guest users can make calls without this code.

OBS Virtual Camera carries video only. After updating Pi, enable **Snail microphone** to try two-way Bluetooth audio. In Telegram / Meet, choose **DenDenMushi** or the system default microphone. The channel may open only when Telegram starts recording. This mode uses mono, telephone-quality audio. Turning it off restores stereo and the previous microphone unless you selected another input meanwhile. The app does not save the call to a file. Verify your voice with a short test recording.

Older Pi services need one update from app settings. The owner enters the Pi account password in the update window; normal connections do not require it.

## Language

In **Settings → App language**, choose **Українська**, **Čeština** or **English**. The change is immediate and remembered on this Mac. Device names and passwords stay unchanged. macOS permission dialogs use the system’s app-language settings.

## Password and Wi-Fi

- **Change the snail’s password** sets the password used to add a Mac. The Pi account password is needed to save this administrative change. It is not stored, and the Pi login password does not change.
- **Code for another Mac** copies an access code to share with an authorized user. Keep it private.
- **Change the snail’s Wi-Fi** sets its network over Bluetooth. Pi 3 needs 2.4 GHz Wi-Fi; isolated guest networks may prevent video from reaching the Mac.
- **Reset this Mac’s connection** removes this Mac’s camera key on Pi and its saved local connection. It copies the snail’s code to the clipboard for a repeat of the first connection. Pi’s Wi-Fi and services stay configured.

## A newly assembled Pi

The owner installs the services once under **Settings → Prepare a new Pi / update the service**. Pi must already be reachable over SSH on the local network. Enter its address, username and account password. Other users receiving a prepared snail only need its password or code.

**Stop** stops the camera. Closing the window leaves the app in the menu bar; **Quit DenDenMushi** stops its managed camera processes and exits.

## Automatic quiet

After updating the Pi, its output mutes about 2 seconds after the Bluetooth stream stops and wakes when playback starts. Manual mute and volume are preserved separately. An app that keeps sending silence in an active stream keeps the output awake. Update once using “Update Pi” and enter the owner’s password in the app window.

## Speakers and microphone through one USB adapter (v0.5.4)

Connect the amplifier input to the green output on the Alza USB adapter, and the microphone to the red input on the same adapter. Update the service with “Prepare my Pi”, then click “Connect”. Speaker volume and automatic mute control USB playback; microphone level controls USB capture. For calls, also enable “Snail microphone” (v0.6.0, experimental mode).

If Mac does not show the microphone after switching from stereo playback, the app reconnects only DenDenMushi once. Audio pauses briefly and pairing is preserved. If recovery fails, click Retry audio.
