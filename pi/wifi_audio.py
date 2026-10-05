"""Duplex PCM over the existing authenticated SSH/loopback HTTP transport.

No LAN listener, recordings, arbitrary targets or shell commands. One explicit
client at a time; EOF, stalled clients and process failures close both devices.
Wire format: fixed 20 ms blocks, 48 kHz stereo signed little-endian 16-bit PCM.
"""
import socket
import shlex
import subprocess
import threading
import time

from audio import AudioError

BLOCK = 3840


def read_block(reader):
    data = bytearray()
    while len(data) < BLOCK:
        part = reader.read(BLOCK - len(data))
        if not part:
            raise EOFError()
        data.extend(part)
    return bytes(data)


class EchoCancellation:
    """Session-owned AEC pair: playback reference and filtered capture together.

    No defaults or hardware gain changes. If unavailable, retain raw endpoints
    and expose the failure in status rather than break working call audio.
    """
    SOURCE = "denden_wifi_aec_source"
    SINK = "denden_wifi_aec_sink"

    def __init__(self, backend):
        self.backend = backend
        self.module = None
        self.active = False
        self.error = None

    def prepare(self, output, source):
        if source is None:
            return output, source
        try:
            # Reap only our named pair left behind by a previous process crash.
            # WiFiAudio admits one session, so no current session owns this pair.
            for module in self.backend.items("modules"):
                if module.get("name") != "module-echo-cancel":
                    continue
                args = dict(part.split("=", 1) for part in shlex.split(module.get("argument") or "") if "=" in part)
                if args.get("source_name") == self.SOURCE and args.get("sink_name") == self.SINK:
                    self.backend.pulse("unload-module", str(module["index"]))
            identity = self.backend.pulse(
                "load-module", "module-echo-cancel", "aec_method=webrtc",
                "source_master=" + source, "sink_master=" + output,
                "source_name=" + self.SOURCE, "sink_name=" + self.SINK,
                # PipeWire otherwise rounded the graph to 256 frames, causing
                # xruns on Pi 3. Keep the active AEC node on its 10ms cadence.
                # Node-scoped force disappears when this module is unloaded.
                "sink_properties=node.force-quantum=480",
                "rate=48000", "channels=2")
            if not identity.isdecimal():
                raise AudioError("echo_cancel_invalid_module")
            self.module = identity
            # Wait briefly for PipeWire to publish both nodes. Never route just
            # one side: AEC needs the exact signal sent to the speakers.
            for _ in range(10):
                sources = self.backend.items("sources")
                sinks = self.backend.items("sinks")
                if (any(x.get("name") == self.SOURCE for x in sources)
                        and any(x.get("name") == self.SINK for x in sinks)):
                    self.active = True
                    return self.SINK, self.SOURCE
                time.sleep(0.05)
            raise AudioError("echo_cancel_endpoints_missing")
        except (AudioError, OSError, ValueError, KeyError):
            self.error = "echo_cancel_unavailable"
            self.close()
            return output, source

    def close(self):
        self.active = False
        if self.module is not None:
            try:
                self.backend.pulse("unload-module", self.module)
            except (AudioError, OSError):
                self.error = "echo_cancel_cleanup_failed"
            finally:
                self.module = None


class WiFiAudio:
    def __init__(self, audio, popen=subprocess.Popen):
        self.audio = audio
        self.popen = popen
        self.lock = threading.Lock()
        self.session = None

    def status(self):
        with self.lock:
            session = self.session
            return {"ok": True, "active": session is not None and not session.closed.is_set(),
                    "microphone": bool(session and session.microphone),
                    "sentBlocks": session.sent if session else 0,
                    "receivedBlocks": session.received if session else 0,
                    "echoCancellation": bool(session and session.echo and session.echo.active),
                    "echoCancellationError": session.echo.error if session and session.echo else None}

    def stop(self):
        with self.lock:
            session = self.session
        if session:
            session.close()
            if not session.finished.wait(6):
                raise AudioError("audio_busy")

    def serve(self, handler, microphone):
        if type(microphone) is not bool:
            raise AudioError("invalid_request")
        with self.lock:
            if self.session is not None:
                raise AudioError("audio_busy")
            session = Session(handler.connection, microphone, self.popen)
            self.session = session
        upgraded = False
        backend = None
        try:
            # Validate endpoints before suspending the working Bluetooth mode.
            backend = self.audio.backend()
            output = backend.audio_device("output")["name"]
            source = backend.audio_device("input")["name"] if microphone else None
            session.echo = EchoCancellation(backend)
            output, source = session.echo.prepare(output, source)
            self.audio.suspend_for_wifi()
            session.start(output, source, backend.env)
            if session.closed.is_set():
                raise AudioError("wifi_audio_closed")
            upgraded = True
            handler.send_response(101)
            handler.send_header("Connection", "Upgrade")
            handler.send_header("Upgrade", "denden-pcm-v1")
            handler.end_headers()
            handler.wfile.flush()
            handler.close_connection = True
            session.run(handler.rfile)
        finally:
            session.close(shutdown=upgraded)
            if session.echo:
                session.echo.close()
            if backend is not None:
                backend.close()
            self.audio.finish_wifi()
            with self.lock:
                if self.session is session:
                    self.session = None
            session.finished.set()


class Session:
    def __init__(self, connection, microphone, popen):
        self.connection = connection
        self.microphone = microphone
        self.popen = popen
        self.closed = threading.Event()
        self.finished = threading.Event()
        self.process_lock = threading.Lock()
        self.processes = []
        self.echo = None
        self.capture = self.playback = None
        self.sent = self.received = 0
        self.last_received = time.monotonic()

    def start(self, output, source, env):
        common = ["--raw", "--format=s16le", "--rate=48000", "--channels=2",
                  "--latency-msec=60", "--process-time-msec=20", "--client-name=DenDenMushi-WiFi"]
        with self.process_lock:
            if self.closed.is_set():
                raise AudioError("wifi_audio_closed")
            self.playback = self.popen(["/usr/bin/pacat", "--playback", "--device=" + output, *common],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env=env, bufsize=0)
            self.processes.append(self.playback)
            if source:
                self.capture = self.popen(["/usr/bin/parec", "--device=" + source, *common],
                                          stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, env=env, bufsize=0)
                self.processes.append(self.capture)
        self.connection.settimeout(3)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)

    def close(self, shutdown=True):
        self.closed.set()
        # Shutdown also wakes the reader if a separate HTTP stop was requested.
        if shutdown:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        with self.process_lock:
            processes, self.processes = self.processes, []
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass

    def run(self, reader):
        def send():
            try:
                while not self.closed.is_set():
                    if self.capture:
                        block = read_block(self.capture.stdout)
                    else:
                        if self.closed.wait(0.02):
                            break
                        block = bytes(BLOCK)
                    self.connection.sendall(block)
                    self.sent += 1
            except (OSError, ValueError, EOFError):
                pass
            finally:
                self.close()

        def watchdog():
            while not self.closed.wait(0.5):
                if (time.monotonic() - self.last_received > 3 or
                        any(p.poll() is not None for p in self.processes)):
                    self.close()
                    break

        sender = threading.Thread(target=send, daemon=True)
        watcher = threading.Thread(target=watchdog, daemon=True)
        sender.start(); watcher.start()
        try:
            while not self.closed.is_set():
                block = read_block(reader)
                self.last_received = time.monotonic()
                # FileIO.write can be short. Never lose channel/frame alignment.
                remaining = memoryview(block)
                while remaining and not self.closed.is_set():
                    count = self.playback.stdin.write(remaining)
                    if not count:
                        raise EOFError()
                    remaining = remaining[count:]
                self.received += 1
        except (OSError, ValueError, EOFError):
            pass
        finally:
            self.close()
            sender.join(timeout=2); watcher.join(timeout=2)
