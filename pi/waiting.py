"""Local waiting audio; never modifies the speaker's master gain or mute."""
import json
import math
import subprocess
import threading
import time
from pathlib import Path


class WaitingSound:
    def __init__(self, audio, camera, wifi, path="/var/lib/denden-audio/waiting.json",
                 media="/opt/denden-demo/waiting.wav", clock=time.monotonic, popen=subprocess.Popen):
        self.audio, self.camera, self.wifi = audio, camera, wifi
        self.path, self.media = Path(path), Path(media)
        self.clock, self.popen = clock, popen
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.process = None
        self.enabled, self.volume = True, 20
        self.until = clock() + 10  # Give boot-time automatic connections time to settle.
        self.error = None
        try:
            data = json.loads(self.path.read_text())
            self.validate(data)
            self.enabled, self.volume = data["enabled"], data["volume"]
        except (OSError, ValueError):
            pass

    @staticmethod
    def validate(data):
        if (not isinstance(data, dict) or set(data) != {"enabled", "volume"}
                or type(data["enabled"]) is not bool
                or type(data["volume"]) not in (int, float)
                or not math.isfinite(data["volume"]) or not 0 <= data["volume"] <= 100):
            raise ValueError("invalid_request")

    def _stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            self.process = None

    def connected(self):
        with self.lock:
            self.until = self.clock() + 35
            self._stop()

    def status(self):
        with self.lock:
            return {"ok": True, "enabled": self.enabled, "volume": self.volume,
                    "playing": self.process is not None and self.process.poll() is None,
                    "error": self.error}

    def configure(self, data):
        self.validate(data)
        with self.lock:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data))
            temporary.replace(self.path)
            self.enabled, self.volume = data["enabled"], data["volume"]
            self._stop()
            return self.status()

    def tick(self):
        with self.lock:
            if (self.closed.is_set() or not self.enabled or self.volume == 0
                    or self.clock() < self.until or self.camera.status()["requested"]
                    or self.wifi.status().get("active")):
                self._stop()
                return
            # Coordinate with the existing routing/mute worker.
            if not self.audio.lock.acquire(timeout=.2):
                return
            backend = None
            try:
                backend = self.audio.backend()
                if any(props.get("org.bluez.Device1", {}).get("Connected")
                       for props in backend.objects().values()):
                    self._stop()
                    return
                sink = backend.audio_device("output")
                if self.process is not None and self.process.poll() is None:
                    return
                self._stop()
                self.process = self.popen(
                    ["paplay", "--device=" + sink["name"], "--client-name=DenDenMushi Waiting",
                     "--volume=" + str(round(65536 * self.volume / 100)), str(self.media)],
                    env=backend.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.error = None
            except Exception as exc:
                self._stop()
                self.error = str(exc)[:120]
            finally:
                if backend is not None:
                    backend.close()
                self.audio.lock.release()

    def run(self):
        try:
            while not self.closed.wait(.5):
                self.tick()
        finally:
            with self.lock:
                self._stop()
