"""Mute the configured output between sessions, without gating quiet samples."""
import json
import os
import time
from pathlib import Path


class QuietOutput:
    def __init__(self, path=None, clock=time.monotonic, delay=2.0):
        self.path = Path(path) if path else None
        self.clock = clock
        self.delay = delay
        self.last_active = clock()
        self.sink = None
        self.manual = False
        self.automatic = False
        self.error = None
        self.saved = None
        if self.path:
            try:
                data = json.loads(self.path.read_text())
                if (isinstance(data, dict) and isinstance(data.get("sink"), str)
                        and type(data.get("manual")) is bool and type(data.get("automatic")) is bool):
                    self.saved = data
            except (OSError, ValueError):
                pass

    def adopt(self, item):
        name, muted = item["name"], bool(item.get("mute"))
        if self.sink != name:
            saved = self.saved
            self.sink = name
            if saved and saved["sink"] == name and muted == (saved["manual"] or saved["automatic"]):
                self.manual, self.automatic = saved["manual"], saved["automatic"]
            else:
                self.manual, self.automatic = muted, False
            self.last_active = self.clock() - self.delay if self.automatic else self.clock()
        elif muted != (self.manual or self.automatic):
            # Honor an observable mute change made outside this service.
            self.manual, self.automatic = muted, False
            self.last_active = self.clock()

    def save(self, manual, automatic):
        data = {"sink": self.sink, "manual": manual, "automatic": automatic}
        if self.path and data != self.saved:
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w") as stream:
                os.chmod(temporary, 0o600)
                json.dump(data, stream)
            temporary.replace(self.path)
        self.saved = data

    def set_state(self, backend, manual, automatic):
        # Persist intent before changing hardware so a service restart can
        # distinguish its own idle mute from the user's mute button.
        self.save(manual, automatic)
        if (manual or automatic) != (self.manual or self.automatic):
            backend.pulse("set-sink-mute", self.sink, "1" if manual or automatic else "0")
        self.manual, self.automatic = manual, automatic

    def tick(self, backend, active_hint=False):
        item = backend.audio_device("output")
        self.adopt(item)
        active = active_hint or backend.playback_active(item)
        if active:
            self.last_active = self.clock()
        automatic = not active and self.clock() - self.last_active >= self.delay
        self.set_state(backend, self.manual, automatic)
        self.error = None

    def manual_level(self, backend, db, muted):
        item = backend.audio_device("output")
        self.adopt(item)
        self.save(muted, self.automatic)
        result = backend.set_level("output", db, muted or self.automatic)
        self.manual = muted
        return result

    def decorate(self, result):
        output = result.get("output", {})
        if self.sink and output.get("available"):
            output["effectiveMuted"] = output["muted"]
            output["muted"] = self.manual
        result["quiet"] = {"enabled": True, "active": self.automatic,
                           "delaySeconds": self.delay, "error": self.error}
        return result

    def release(self, backend):
        # Stop only our automatic mute; never undo a user-requested mute.
        item = backend.audio_device("output")
        self.adopt(item)
        self.set_state(backend, self.manual, False)
