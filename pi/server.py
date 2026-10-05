#!/usr/bin/env python3
"""Loopback-only camera controller. Remote access must use an SSH tunnel."""
import json
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Camera:
    def __init__(self, lease_seconds=30, popen=subprocess.Popen):
        self.lock = threading.RLock()
        self.desired = False
        self.last_seen = 0.0
        self.lease_seconds = lease_seconds
        self.processes = []
        self.error = None
        self.restarts = 0
        self.popen = popen
        self.closed = threading.Event()

    def heartbeat(self):
        with self.lock:
            self.last_seen = time.monotonic()

    def start(self):
        with self.lock:
            self.desired = True
            self.last_seen = time.monotonic()

    def stop(self):
        with self.lock:
            self.desired = False
            self._stop_processes()

    def _stop_processes(self):
        for p in self.processes:
            if p.poll() is None:
                p.terminate()
        for p in self.processes:
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
        self.processes = []

    def tick(self):
        with self.lock:
            if self.desired and time.monotonic() - self.last_seen > self.lease_seconds:
                self.desired = False
            if not self.desired:
                self._stop_processes()
                return
            if self.processes and all(p.poll() is None for p in self.processes):
                return
            self._stop_processes()
            # Keep 720p30, but leave wireless airtime for simultaneous Bluetooth
            # voice on the Pi 3. The former 4 Mbps stream coincided with audio
            # gaps that disappeared when camera transmission was paused.
            # The TCP listener is reachable only through SSH; recover lost OBS.
            try:
                capture = self.popen([
                    "rpicam-vid", "--timeout", "0", "--nopreview", "--width", "1280",
                    "--height", "720", "--framerate", "30", "--codec", "h264",
                    "--bitrate", "1500000", "--inline", "--intra", "30", "--output", "-"
                ], stdout=subprocess.PIPE)
                self.processes = [capture]
                mux = self.popen([
                    "ffmpeg", "-hide_banner", "-loglevel", "warning", "-probesize", "32768",
                    "-analyzeduration", "0", "-fflags", "+genpts", "-r", "30", "-f", "h264",
                    "-i", "pipe:0", "-c:v", "copy", "-an", "-f", "mpegts",
                    "-flush_packets", "1", "tcp://127.0.0.1:8554?listen=1"
                ], stdin=capture.stdout)
                capture.stdout.close()
                self.processes.append(mux)
                self.error = None
                self.restarts += 1
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                self._stop_processes()

    def status(self):
        with self.lock:
            return {"requested": self.desired,
                    "processesRunning": len(self.processes) == 2 and
                        all(p.poll() is None for p in self.processes),
                    "starts": self.restarts, "error": self.error}

    def run(self):
        while not self.closed.wait(2):
            self.tick()
        self.stop()


def handler_for(camera, audio=None, servos=None, wifi=None, waiting=None, history=None):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            # Block browser cross-origin requests to the forwarded controller.
            return self.headers.get("X-DenDen-Client") == "desktop-v1" and not self.headers.get("Origin")

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {"error": "native client required"})
            if self.path.startswith("/wifi/networks?"):
                from urllib.parse import parse_qs, urlsplit
                import pwd
                from provision_backend import Backend
                try:
                    offset = int(parse_qs(urlsplit(self.path).query).get("offset", ["0"])[0])
                    return self.reply(200, Backend(pwd.getpwuid(os.getuid()).pw_name).saved_wifi(offset))
                except Exception:
                    return self.reply(503, {"error": "wifi_list_unavailable"})
            if self.path == "/devices/history" and history is not None:
                return self.reply(200, history.status())
            if self.path == "/audio/waiting" and waiting is not None:
                return self.reply(200, waiting.status())
            if waiting is not None and self.path in ("/audio/wifi/stream", "/servos/status"):
                waiting.connected()
            if self.path == "/audio/wifi/status" and wifi is not None:
                return self.reply(200, wifi.status())
            if self.path == "/audio/wifi/stream" and wifi is not None:
                from audio import AudioError
                mic = self.headers.get("X-DenDen-Microphone")
                if (mic not in ("0", "1") or self.headers.get("Upgrade") != "denden-pcm-v1"
                        or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length") not in (None, "0")):
                    return self.reply(400, {"ok": False, "error": "invalid_request"})
                try:
                    return wifi.serve(self, mic == "1")
                except AudioError as exc:
                    return self.reply(409, {"ok": False, "error": str(exc)})
                except (OSError, ValueError):
                    return self.reply(503, {"ok": False, "error": "wifi_audio_unavailable"})
            if self.path == "/servos/status" and servos is not None:
                return self.reply(200, servos.command({"action": "status"}))
            if self.path in ("/audio/levels", "/audio/status") and audio is not None:
                from audio import AudioError
                try:
                    return self.reply(200, audio.levels() if self.path == "/audio/levels" else audio.status())
                except AudioError as exc:
                    return self.reply(200, {"ok": False, "error": str(exc), "detail": exc.detail})
            if self.path != "/status":
                return self.reply(404, {"error": "not found"})
            features = ["audio-connect-v1", "audio-levels-v1", "audio-split-io-v1", "audio-call-v1", "audio-mic-gain-v1"] if audio else []
            if audio and getattr(audio, "quiet", None):
                features.append("audio-idle-mute-v1")
            if history is not None:
                features.append("device-history-v1")
            if waiting is not None:
                features.append("waiting-audio-v1")
            if wifi is not None:
                features.append("audio-wifi-v1")
            if servos is not None:
                features.append("servos-v1")
                features.append("servos-hold-v1")
                features.append("servos-pulse-v2")
                features.append("servos-sweep-v1")
            self.reply(200, {"service": "denden", "version": 1, "features": features, "camera": camera.status()})

        def do_POST(self):
            if not self.allowed():
                return self.reply(403, {"error": "native client required"})
            if self.path == "/audio/waiting" and waiting is not None:
                try:
                    self.connection.settimeout(3)
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= size <= 512 or self.headers.get("Transfer-Encoding"):
                        raise ValueError("invalid_request")
                    return self.reply(200, waiting.configure(json.loads(self.rfile.read(size))))
                except (ValueError, OSError):
                    return self.reply(400, {"ok": False, "error": "invalid_request"})
            if history is not None and self.path in ("/camera/start", "/heartbeat"):
                history.desktop(self.headers.get("X-DenDen-Device-ID"), self.headers.get("X-DenDen-Device-Name"))
            if waiting is not None and self.path in ("/camera/start", "/heartbeat", "/audio/connect", "/servos/command"):
                waiting.connected()
            if self.path == "/audio/wifi/stop" and wifi is not None:
                from audio import AudioError
                try:
                    wifi.stop()
                except AudioError as exc:
                    return self.reply(409, {"ok": False, "error": str(exc)})
                return self.reply(200, wifi.status())
            if self.path == "/servos/command" and servos is not None:
                from servos import validate
                try:
                    self.connection.settimeout(2)
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= size <= 512 or self.headers.get("Transfer-Encoding"):
                        raise ValueError("invalid_request")
                    body = json.loads(self.rfile.read(size))
                    validate(body)
                except (ValueError, OSError):
                    return self.reply(400, {"ok": False, "error": "invalid_request"})
                return self.reply(200, servos.command(body))
            if self.path in ("/audio/connect", "/audio/level") and audio is not None:
                from audio import AudioError
                try:
                    self.connection.settimeout(5)
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= size <= 512 or self.headers.get("Transfer-Encoding"):
                        return self.reply(400, {"ok": False, "error": "invalid_request"})
                    body = json.loads(self.rfile.read(size))
                    fields = ({"address"}, {"address", "mode"}) if self.path == "/audio/connect" else ({"kind", "db", "muted"},)
                    if not isinstance(body, dict) or set(body) not in fields:
                        return self.reply(400, {"ok": False, "error": "invalid_request"})
                    if self.path == "/audio/connect":
                        if body.get("mode", "playback") not in ("playback", "call"):
                            return self.reply(400, {"ok": False, "error": "invalid_mode"})
                        if wifi is not None:
                            wifi.stop()
                        return self.reply(200, audio.connect(body["address"], body.get("mode", "playback")))
                    return self.reply(200, audio.set_level(body["kind"], body["db"], body["muted"]))
                except (ValueError, OSError):
                    return self.reply(400, {"ok": False, "error": "invalid_request"})
                except AudioError as exc:
                    return self.reply(200, {"ok": False, "error": str(exc), "detail": exc.detail})
            if self.path == "/camera/start":
                camera.start()
            elif self.path == "/camera/stop":
                camera.stop()
            elif self.path == "/heartbeat":
                try:
                    self.connection.settimeout(5)
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 <= size <= 512 or self.headers.get("Transfer-Encoding"):
                        raise ValueError("invalid heartbeat")
                    body = json.loads(self.rfile.read(size)) if size else {}
                    if (not isinstance(body, dict) or set(body) not in (set(), {"microphone"})
                            or ("microphone" in body and type(body["microphone"]) is not bool)):
                        raise ValueError("invalid heartbeat")
                except (ValueError, OSError):
                    return self.reply(400, {"ok": False, "error": "invalid_request"})
                if audio:
                    audio.heartbeat(body.get("microphone", False))
                camera.heartbeat()
            else:
                return self.reply(404, {"error": "not found"})
            self.reply(200, camera.status())

        def log_message(self, *_):
            pass
    return Handler


def main():
    from audio import Audio
    from quiet import QuietOutput
    from servos import ServoClient
    from wifi_audio import WiFiAudio
    camera = Camera()
    audio = Audio(quiet=QuietOutput("/var/lib/denden-audio/quiet.json"))
    worker = threading.Thread(target=camera.run, daemon=True)
    worker.start()
    audio_worker = threading.Thread(target=audio.run, daemon=True)
    audio_worker.start()
    servos = ServoClient()
    wifi = WiFiAudio(audio)
    from waiting import WaitingSound
    waiting = WaitingSound(audio, camera, wifi)
    waiting_worker = threading.Thread(target=waiting.run, daemon=True)
    waiting_worker.start()
    from device_history import DeviceHistory
    history = DeviceHistory()
    history_worker = threading.Thread(target=history.run, daemon=True)
    history_worker.start()
    server = ThreadingHTTPServer(("127.0.0.1", 8789), handler_for(camera, audio, servos, wifi, waiting, history))
    server.daemon_threads = True
    def shutdown(*_):
        history.closed.set()
        waiting.closed.set()
        camera.closed.set()
        audio.closed.set()
        audio.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        history.closed.set()
        waiting.closed.set()
        history_worker.join(timeout=6)
        waiting_worker.join(timeout=6)
        wifi.stop()
        servos.command({"action": "stop"})
        camera.closed.set()
        audio.closed.set()
        audio.wake.set()
        worker.join(timeout=10)
        audio_worker.join(timeout=10)
        server.server_close()


if __name__ == "__main__":
    main()
