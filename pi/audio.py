"""Route a paired Mac to the configured speakers; capture stays on USB.

Only reachable through the existing loopback HTTP / SSH control path. Never
pairs, trusts, scans for devices, or accepts arbitrary shell commands.
"""
import json
import math
import os
import re
import shlex
import subprocess
import threading
import time


class AudioError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(code)
        self.detail = detail[:240]


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}", value):
        raise AudioError("invalid_address")
    return value.upper()


class Backend:
    def __init__(self):
        # A private connection per HTTP worker; do not share D-Bus connections
        # between the camera's HTTP threads.
        import dbus
        self.dbus = dbus
        self.bus = dbus.SystemBus(private=True)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}",
                        PULSE_SERVER=f"unix:/run/user/{os.getuid()}/pulse/native")

    def objects(self):
        obj = self.bus.get_object("org.bluez", "/")
        return self.dbus.Interface(obj, "org.freedesktop.DBus.ObjectManager").GetManagedObjects(timeout=4)

    def device(self, mac):
        matches = [(str(path), interfaces["org.bluez.Device1"]) for path, interfaces in self.objects().items()
                   if interfaces.get("org.bluez.Device1", {}).get("Address", "").upper() == mac]
        if len(matches) != 1:
            raise AudioError("pair_required")
        path, properties = matches[0]
        if not properties.get("Paired") or not properties.get("Trusted"):
            raise AudioError("pair_required")
        return path, str(properties["Adapter"])

    def adapter_address(self, adapter):
        return address(str(self.objects()[adapter]["org.bluez.Adapter1"]["Address"]))

    def ensure_power(self, adapter):
        # Only an explicit connect to an already trusted peer can power the
        # adapter on. Idle monitoring and level changes never change radio power.
        try:
            props = self.dbus.Interface(self.bus.get_object("org.bluez", adapter),
                                        "org.freedesktop.DBus.Properties")
            if not props.Get("org.bluez.Adapter1", "Powered", timeout=4):
                props.Set("org.bluez.Adapter1", "Powered", self.dbus.Boolean(True), timeout=8)
            if not props.Get("org.bluez.Adapter1", "Powered", timeout=4):
                raise AudioError("bluetooth_power_unavailable")
        except self.dbus.DBusException as exc:
            raise AudioError("bluetooth_power_unavailable", str(exc)) from exc

    def connect_profile(self, path, mode="playback"):
        profile = "0000111f-0000-1000-8000-00805f9b34fb" if mode == "call" else "0000110a-0000-1000-8000-00805f9b34fb"
        deadline = time.monotonic() + 12
        last_error = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AudioError("bluetooth_unavailable", str(last_error) if last_error else "Bluetooth profile connection timed out") from last_error
            try:
                self.dbus.Interface(self.bus.get_object("org.bluez", path), "org.bluez.Device1").ConnectProfile(
                    profile, timeout=remaining)
                return
            except self.dbus.DBusException as exc:
                name = exc.get_dbus_name()
                if name == "org.bluez.Error.AlreadyConnected":
                    return
                # BlueZ can still be completing the preceding connection when
                # the app retries. Keep the same peer/profile intact, allowing
                # only this transitional error within the original time budget.
                if name != "org.bluez.Error.InProgress":
                    raise AudioError("bluetooth_unavailable", str(exc)) from exc
                last_error = exc
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(0.25, remaining))

    def disconnect_call_profile(self, path):
        try:
            self.dbus.Interface(self.bus.get_object("org.bluez", path), "org.bluez.Device1").DisconnectProfile(
                "0000111f-0000-1000-8000-00805f9b34fb", timeout=5)
        except self.dbus.DBusException as exc:
            if exc.get_dbus_name() not in ("org.bluez.Error.NotConnected", "org.bluez.Error.DoesNotExist"):
                raise AudioError("call_stop_failed", str(exc)) from exc

    def pulse(self, *args):
        try:
            result = subprocess.run(["pactl", *args], env=self.env, capture_output=True,
                                    text=True, timeout=3, check=True)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise AudioError("audio_service_unavailable", f"{type(exc).__name__}: {str(getattr(exc, 'stderr', None) or exc)}") from exc

    def items(self, kind):
        try:
            return json.loads(self.pulse("--format=json", "list", kind))
        except ValueError as exc:
            raise AudioError("audio_service_unavailable", f"{type(exc).__name__}: {str(getattr(exc, 'stderr', None) or exc)}") from exc

    def route(self, mac):
        sink = self.audio_device("output")["name"]
        sources = []
        for _ in range(8):
            sources = [s for s in self.items("sources")
                       if s.get("name", "").startswith("bluez_input.")
                       and (mac in s["name"].upper() or mac.replace(":", "_") in s["name"].upper()
                            or s.get("properties", {}).get("api.bluez5.address", "").upper() == mac)]
            if sources:
                break
            time.sleep(0.3)
        self.pulse("set-default-sink", sink)
        try:
            microphone = self.audio_device("input")
        except AudioError as exc:
            if not str(exc).startswith("usb_input_"):
                raise
        else:
            self.pulse("set-default-source", microphone["name"])
        # Remove only an old route created by this application for the same
        # Bluetooth source; otherwise switching jacks can leave USB playing too.
        source_names = {item["name"] for item in sources}
        for module in self.items("modules"):
            if module.get("name") != "module-loopback":
                continue
            args = shlex.split(module.get("argument") or "")
            owned = {"latency_msec=60", "source_dont_move=true", "sink_dont_move=true"}.issubset(args)
            same_source = any(f"source={name}" in args for name in source_names)
            if owned and same_source and f"sink={sink}" not in args:
                self.pulse("unload-module", str(module["index"]))
        # In playback mode PipeWire routes the Bluetooth stream directly; in
        # input mode it exposes a capture source and needs a loopback.
        streams = [s for s in self.items("sink-inputs")
                   if mac.replace(":", "_") in str(s.get("properties", {}).get("node.name", "")).upper()
                   or mac in str(s.get("properties", {}).get("node.name", "")).upper()
                   or s.get("properties", {}).get("api.bluez5.address", "").upper() == mac]
        if streams:
            for stream in streams:
                self.pulse("move-sink-input", str(stream["index"]), sink)
            return sink
        if not sources:
            # Idle playback streams may be created only when the Mac starts
            # sending audio. Their target is now the configured default.
            return sink
        if len(sources) != 1:
            raise AudioError("bluetooth_source_missing")
        source = sources[0]["name"]
        # PipeWire loopbacks survive between app connections; reuse the exact
        # source/sink route instead of doubling playback on every click.
        exists = False
        for module in self.items("modules"):
            if module.get("name") == "module-loopback":
                args = shlex.split(module.get("argument") or "")
                if f"source={source}" in args and f"sink={sink}" in args:
                    exists = True
        self.pulse("set-default-sink", sink)
        if not exists:
            self.pulse("load-module", "module-loopback", f"source={source}", f"sink={sink}",
                       "latency_msec=60", "source_dont_move=true", "sink_dont_move=true")
        return sink

    @staticmethod
    def peer_item(item, mac):
        props = item.get("properties", {})
        value = str(props.get("api.bluez5.address", "")).upper()
        if value:
            return value == mac
        # Device indices and display names are not identities. Older Pulse
        # mappings sometimes expose the address only in a generated node name.
        for name in (item.get("name", ""), props.get("node.name", ""), props.get("device.name", "")):
            for formatted in (mac, mac.replace(":", "_")):
                if re.match(r"^bluez_(?:card|input|output)\." + re.escape(formatted) + r"(?:[.:]|$)", str(name), re.I):
                    return True
        return False

    @staticmethod
    def sco_item(item):
        props = item.get("properties", {})
        return str(props.get("api.bluez5.profile", "")).lower() in ("headset-audio-gateway", "hfp-ag", "hfp_ag")

    def peer_card(self, mac):
        cards = [item for item in self.items("cards") if self.peer_item(item, mac)]
        if len(cards) != 1:
            raise AudioError("bluetooth_card_missing" if not cards else "bluetooth_card_ambiguous")
        return cards[0]

    def prepare_call(self, mac):
        # Check local physical endpoints before changing the peer's profile.
        usb_input = self.audio_device("input")
        speaker_output = self.audio_device("output")
        # BlueZ can finish ConnectProfile before PipeWire publishes the card
        # and its profiles. Keep that same HFP connection alive while waiting;
        # failing immediately would disconnect it again in connect() cleanup.
        deadline = time.monotonic() + 4
        while True:
            try:
                card = self.peer_card(mac)
                profiles = card.get("profiles", {})
                if isinstance(profiles, list):
                    profiles = {p.get("name"): p for p in profiles}
                profile = profiles.get("audio-gateway")
                if not profile or str(profile.get("available", "unknown")).lower() in ("no", "false", "0"):
                    raise AudioError("hfp_profile_unavailable")
                break
            except AudioError as exc:
                if str(exc) not in ("bluetooth_card_missing", "hfp_profile_unavailable"):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.25, remaining))
        # Native HFP capture streams are created when the Mac first records.
        # Give them the intended physical endpoints before profile activation,
        # instead of waiting for a later reconciliation to move them there.
        self.pulse("set-default-source", usb_input["name"])
        self.pulse("set-default-sink", speaker_output["name"])
        active = card.get("active_profile", "")
        if isinstance(active, dict):
            active = active.get("name", "")
        if active != "audio-gateway":
            self.pulse("set-card-profile", card["name"], "audio-gateway")

    @staticmethod
    def call_tag(mac, role):
        return "denden-call-v1-" + mac.replace(":", "") + "-" + role

    @staticmethod
    def module_args(module):
        try:
            return dict(part.split("=", 1) for part in shlex.split(module.get("argument") or "") if "=" in part)
        except ValueError:
            return {}

    def owned_call_modules(self, mac, role=None, snapshot=None):
        roles = (role,) if role else ("microphone", "speaker")
        tags = {"denden.route=" + self.call_tag(mac, r) for r in roles}
        modules = self.items("modules") if snapshot is None else snapshot
        return [m for m in modules if m.get("name") == "module-loopback"
                and self.module_args(m).get("source_output_properties") in tags
                and self.module_args(m).get("sink_input_properties") == self.module_args(m).get("source_output_properties")]

    def legacy_call_module(self, module, mac):
        args = self.module_args(module)
        return (module.get("name") == "module-loopback" and "source_output_properties" not in args
                and args.get("latency_msec") == "60" and args.get("source_dont_move") == "true"
                and args.get("sink_dont_move") == "true"
                and self.peer_item({"name": args.get("source", "")}, mac))

    def remove_call_routes(self, mac):
        for module in self.owned_call_modules(mac):
            self.pulse("unload-module", str(module["index"]))

    def ensure_call_loopback(self, mac, role, source, sink):
        # Never infer a microphone target from the current default sink.
        if role == "microphone" and not (source["name"].startswith("alsa_input.usb-") or source.get("properties", {}).get("device.bus") == "usb"):
            raise AudioError("usb_input_missing")
        if role == "microphone" and not (self.peer_item(sink, mac) and self.sco_item(sink)):
            raise AudioError("bluetooth_microphone_target_missing")
        reusable = None
        for module in self.owned_call_modules(mac, role):
            args = self.module_args(module)
            if reusable is None and args.get("source") == source["name"] and args.get("sink") == sink["name"]:
                reusable = module
            else:
                self.pulse("unload-module", str(module["index"]))
        if reusable is None:
            marker = "denden.route=" + self.call_tag(mac, role)
            self.pulse("load-module", "module-loopback", "source=" + source["name"], "sink=" + sink["name"],
                       "latency_msec=60", "source_dont_move=true", "sink_dont_move=true",
                       "source_output_properties=" + marker, "sink_input_properties=" + marker)

    def call_snapshot(self, mac):
        card = self.peer_card(mac)
        path, _ = self.device(mac)
        connected = bool(self.objects().get(path, {}).get("org.bluez.Device1", {}).get("Connected"))
        sources = self.items("sources")
        sinks = self.items("sinks")
        peer_sources = [x for x in sources if self.peer_item(x, mac) and not x.get("name", "").endswith(".monitor")]
        peer_sinks = [x for x in sinks if self.peer_item(x, mac)]
        inputs = [x for x in peer_sources if self.sco_item(x)]
        outputs = [x for x in peer_sinks if self.sco_item(x)]
        streams = [x for x in self.items("sink-inputs") if self.peer_item(x, mac) and self.sco_item(x)]
        mic_streams = [x for x in self.items("source-outputs") if self.peer_item(x, mac) and self.sco_item(x)]
        active = card.get("active_profile", "")
        if isinstance(active, dict):
            active = active.get("name", "")
        return {"card": card, "profile": active, "peerConnected": connected,
                "sources": peer_sources, "sinks": peer_sinks, "scoSources": inputs, "scoSinks": outputs,
                "speakerStreams": streams, "microphoneStreams": mic_streams,
                "usbInput": self.audio_device("input", sources), "usbOutput": self.audio_device("output", sinks)}

    def call_route_ready(self, mac, role, source, sink):
        tag = self.call_tag(mac, role)
        modules = [m for m in self.owned_call_modules(mac, role)
                   if self.module_args(m).get("source") == source["name"] and self.module_args(m).get("sink") == sink["name"]]
        if len(modules) != 1:
            return False
        module = str(modules[0]["index"])
        def linked(item, endpoint, key):
            owned = str(item.get("owner_module")) == module or item.get("properties", {}).get("denden.route") == tag
            return owned and str(item.get(key)) == str(endpoint.get("index")) and item.get("corked") is False
        return (any(linked(s, source, "source") for s in self.items("source-outputs"))
                and any(linked(s, sink, "sink") for s in self.items("sink-inputs")))

    def call_status(self, mac, snapshot=None):
        snap = snapshot or self.call_snapshot(mac)
        inputs, outputs = snap["scoSources"], snap["scoSinks"]
        valid = snap["peerConnected"] and snap["profile"] == "audio-gateway"
        mic_streams = snap["microphoneStreams"]
        mic_ready = valid and ((len(mic_streams) == 1 and str(mic_streams[0].get("source")) == str(snap["usbInput"].get("index"))
                               and mic_streams[0].get("corked") is False)
                              or (not mic_streams and len(outputs) == 1 and self.call_route_ready(mac, "microphone", snap["usbInput"], outputs[0])))
        speaker_ready = valid and ((len(inputs) == 1 and self.call_route_ready(mac, "speaker", inputs[0], snap["usbOutput"]))
                                   or any(str(s.get("sink")) == str(snap["usbOutput"].get("index")) and s.get("corked") is False for s in snap["speakerStreams"]))
        def public(item):
            props = item.get("properties", {})
            return {"name": item.get("name", props.get("node.name", "")), "profile": props.get("api.bluez5.profile", ""),
                    "codec": props.get("api.bluez5.codec", ""), "state": item.get("state", ""), "corked": item.get("corked"),
                    "muted": item.get("mute"), "volume": item.get("volume", {}),
                    "index": item.get("index"), "source": item.get("source"), "sink": item.get("sink")}
        def silent(item):
            values = [v.get("value") for v in item.get("volume", {}).values() if isinstance(v, dict)]
            return item.get("mute") is True or bool(values and all(v == 0 for v in values))
        mic_muted = any(silent(item) for item in [snap["usbInput"], *mic_streams, *outputs])
        result = {"microphoneReady": bool(mic_ready and not mic_muted), "speakerReady": bool(speaker_ready),
                  "microphoneRouteReady": bool(mic_ready), "microphoneMuted": mic_muted,
                  "peerConnected": snap["peerConnected"], "profile": snap["profile"], "card": snap["card"]["name"],
                  "usbInput": snap["usbInput"]["name"], "sink": snap["usbOutput"]["name"],
                  "usbCapture": public(snap["usbInput"]), "speakerOutput": public(snap["usbOutput"]),
                  "bluetoothInputs": [public(x) for x in snap["sources"][:8]],
                  "bluetoothOutputs": [public(x) for x in snap["sinks"][:8]],
                  "microphoneStreams": [public(x) for x in mic_streams[:8]],
                  "speakerStreams": [public(x) for x in snap["speakerStreams"][:8]]}
        if mic_muted:
            result["microphoneBlockReason"] = "muted_or_zero_volume"
        if not result["microphoneReady"] or not speaker_ready:
            result["error"] = "microphone_pending" if snap["peerConnected"] else "bluetooth_disconnected"
        return result

    def reconcile_call(self, mac):
        snap = self.call_snapshot(mac)
        if not snap["peerConnected"] or snap["profile"] != "audio-gateway":
            self.remove_call_routes(mac)
            return self.call_status(mac, snap)
        if len(snap["scoSinks"]) > 1 or len(snap["scoSources"]) > 1 or len(snap["microphoneStreams"]) > 1:
            self.remove_call_routes(mac)
            raise AudioError("bluetooth_call_ambiguous")
        microphone, speakers = snap["microphoneStreams"], snap["speakerStreams"]
        # An established native SCO stream needs no routing mutation. Use only
        # this tick's freshly validated endpoints/streams, and still check for
        # stale owned modules before skipping maintenance and another snapshot.
        # Missing/recreated/corked nodes take the normal reconciliation below.
        if (not snap["scoSources"] and not snap["scoSinks"]
                and len(microphone) == 1 and len(speakers) == 1
                and snap["usbInput"].get("index") is not None and snap["usbOutput"].get("index") is not None
                and str(microphone[0].get("source")) == str(snap["usbInput"]["index"])
                and str(speakers[0].get("sink")) == str(snap["usbOutput"]["index"])
                and microphone[0].get("corked") is False and speakers[0].get("corked") is False):
            modules = self.items("modules")
            if (not self.owned_call_modules(mac, snapshot=modules)
                    and not any(self.legacy_call_module(module, mac) for module in modules)):
                return self.call_status(mac, snap)
        # Route a newly opened recording before slower module maintenance.
        # Snapshot validation above has already selected one exact peer and
        # physical USB input; unrelated capture streams remain untouched.
        for stream in snap["microphoneStreams"]:
            if str(stream.get("source")) != str(snap["usbInput"].get("index")):
                self.pulse("move-source-output", str(stream["index"]), snap["usbInput"]["name"])
        for stream in snap["speakerStreams"]:
            if str(stream.get("sink")) != str(snap["usbOutput"].get("index")):
                self.pulse("move-sink-input", str(stream["index"]), snap["usbOutput"]["name"])
        # Drop stale owned routes even when the replacement nodes have not
        # appeared yet; module-loopback must never fall back to another device.
        expected = {}
        if snap["scoSinks"] and not snap["microphoneStreams"]:
            expected["microphone"] = (snap["usbInput"], snap["scoSinks"][0])
        if snap["scoSources"] and not snap["speakerStreams"]:
            expected["speaker"] = (snap["scoSources"][0], snap["usbOutput"])
        for role in ("microphone", "speaker"):
            if role not in expected:
                for module in self.owned_call_modules(mac, role):
                    self.pulse("unload-module", str(module["index"]))
        # Migrate only legacy loopbacks our previous version created for this
        # peer. Other modules, users and Bluetooth peers remain untouched.
        for module in self.items("modules"):
            if self.legacy_call_module(module, mac):
                self.pulse("unload-module", str(module["index"]))
        self.pulse("set-default-sink", snap["usbOutput"]["name"])
        self.pulse("set-default-source", snap["usbInput"]["name"])
        for role, (source, sink) in expected.items():
            self.ensure_call_loopback(mac, role, source, sink)
        return self.call_status(mac)

    def close(self):
        self.bus.close()

    def playback_active(self, sink):
        # Pending arrives before PipeWire acquires a Bluetooth transport. Wake
        # the DAC then, instead of waiting for a loud-enough audio sample.
        if any(interfaces.get("org.bluez.MediaTransport1", {}).get("State") in ("pending", "active")
               for interfaces in self.objects().values()):
            return True
        loopbacks = set()
        for module in self.items("modules"):
            if module.get("name") == "module-loopback":
                args = shlex.split(module.get("argument") or "")
                if any(arg.startswith("source=bluez_input.") for arg in args):
                    loopbacks.add(str(module["index"]))
        for stream in self.items("sink-inputs"):
            if str(stream.get("sink")) != str(sink.get("index")) or stream.get("corked", True):
                continue
            props = stream.get("properties", {})
            bluetooth = (str(props.get("node.name", "")).startswith("bluez_")
                         or props.get("api.bluez5.address")
                         or str(stream.get("owner_module")) in loopbacks)
            # Local playback (including a speaker test) must remain audible.
            # An idle Bluetooth loopback alone must not hold the DAC awake.
            if not bluetooth:
                return True
        return False

    def usb_device(self, kind, snapshot=None):
        items = self.items("sinks" if kind == "output" else "sources") if snapshot is None else snapshot
        devices = [item for item in items
                   if (item.get("properties", {}).get("device.bus") == "usb"
                       or item.get("name", "").startswith("alsa_" + ("output" if kind == "output" else "input") + ".usb-"))
                   and not item.get("name", "").endswith(".monitor")]
        if len(devices) != 1:
            raise AudioError("usb_" + kind + ("_missing" if not devices else "_ambiguous"))
        return devices[0]

    def audio_device(self, kind, snapshot=None):
        if kind == "input" or os.environ.get("DENDEN_SPEAKER_OUTPUT", "usb") == "usb":
            return self.usb_device(kind, snapshot)
        if os.environ.get("DENDEN_SPEAKER_OUTPUT") != "headphones":
            raise AudioError("output_configuration_invalid")
        devices = []
        for item in self.items("sinks") if snapshot is None else snapshot:
            props = item.get("properties", {})
            # Never choose an HDMI/USB endpoint by its order or display label.
            if props.get("device.bus") == "usb" or item.get("name", "").startswith("alsa_output.usb-"):
                continue
            card_name = props.get("alsa.card_name") or props.get("api.alsa.card.name")
            match = card_name == "bcm2835 Headphones"
            card = str(props.get("api.alsa.card", props.get("alsa.card", "")))
            if not match and re.fullmatch(r"[0-9]{1,3}", card):
                try:
                    with open(f"/proc/asound/card{card}/id") as stream:
                        match = stream.read().strip() == "Headphones"
                except OSError:
                    pass
            if match:
                devices.append(item)
        if len(devices) != 1:
            raise AudioError("headphone_output_missing" if not devices else "headphone_output_ambiguous")
        return devices[0]

    def levels(self):
        result = {"ok": True}
        for kind in ("output", "input"):
            try:
                item = self.audio_device(kind)
                # PulseAudio's normalized volume is cubic in signal amplitude.
                values = [float(v["value"]) for v in item.get("volume", {}).values()]
                if not values:
                    raise AudioError("audio_service_unavailable")
                value = max(values)
                db = 60 * math.log10(value / 65536) if value > 0 else -60
                result[kind] = {"available": True, "db": max(-60, min(20 if kind == "input" else 0, db)), "muted": bool(item.get("mute"))}
                result[kind]["connection"] = "headphones" if kind == "output" and os.environ.get("DENDEN_SPEAKER_OUTPUT", "usb") == "headphones" else "usb"
            except AudioError as exc:
                if not str(exc).startswith(("usb_", "headphone_output_")):
                    raise
                result[kind] = {"available": False, "error": str(exc)}
        return result

    def set_level(self, kind, db, muted):
        item = self.audio_device(kind)
        target = "sink" if kind == "output" else "source"
        self.pulse("set-" + target + "-volume", item["name"], f"{db:.2f}dB")
        self.pulse("set-" + target + "-mute", item["name"], "1" if muted else "0")
        return self.levels()


class Audio:
    def __init__(self, backend=Backend, quiet=None, clock=time.monotonic):
        self.backend = backend
        self.lock = threading.Lock()
        self.lease_lock = threading.Lock()
        self.quiet = quiet
        self.closed = threading.Event()
        self.wake = threading.Event()
        self.active_hint_until = 0.0
        self.clock = clock
        self.mode = "playback"
        self.peer = None
        self.peer_path = None
        self.adapter = None
        self.call_until = 0.0
        self.call_state = {}
        self.call_error = None
        self.playback_sink = None

    def heartbeat(self, microphone=False):
        if type(microphone) is not bool:
            raise AudioError("invalid_request")
        with self.lease_lock:
            # A camera-only client must never keep a forgotten mic open.
            if microphone and self.mode == "call" and self.call_until > self.clock():
                self.call_until = self.clock() + 30
            elif not microphone:
                self.call_until = 0
                self.wake.set()

    def connection_state(self):
        requested = self.mode == "call" and self.call_until > self.clock()
        result = dict(self.call_state) if self.mode == "call" else {"sink": self.playback_sink}
        mic = requested and not self.call_error and result.get("microphoneReady", False)
        speaker = (requested and not self.call_error and result.get("speakerReady", False)) if self.mode == "call" else bool(self.playback_sink)
        result.update({"ok": bool(mic and speaker) if self.mode == "call" else True,
                       "mode": self.mode, "adapterAddress": self.adapter, "peerAddress": self.peer,
                       "callRequested": requested, "microphoneReady": bool(mic), "speakerReady": bool(speaker)})
        if self.call_error:
            result["error"] = self.call_error
        elif requested and not (mic and speaker):
            result["error"] = result.get("error", "microphone_pending")
        else:
            result.pop("error", None)
        return result

    def status(self):
        if not self.lock.acquire(timeout=1):
            raise AudioError("audio_busy")
        backend = None
        try:
            if self.mode == "call" and self.call_until > self.clock():
                backend = self.backend()
                try:
                    self.call_state = backend.call_status(self.peer)
                    self.call_error = None
                except AudioError as exc:
                    self.call_error = str(exc)
                    self.call_state = {"microphoneReady": False, "speakerReady": False, "detail": exc.detail}
            return self.connection_state()
        finally:
            if backend:
                backend.close()
            self.lock.release()

    def stop_call(self, backend, reason=None):
        with self.lease_lock:
            self.call_until = 0
        self.call_state = {"microphoneReady": False, "speakerReady": False}
        self.call_error = reason
        if self.peer:
            try:
                backend.remove_call_routes(self.peer)
            finally:
                # Direct AG capture is not a loopback: always attempt to close
                # it, even when a stale app-owned module cannot be unloaded.
                backend.disconnect_call_profile(self.peer_path)
        self.mode = "playback"
        self.playback_sink = None

    def suspend_for_wifi(self):
        if not self.lock.acquire(timeout=2):
            raise AudioError("audio_busy")
        backend = None
        try:
            backend = self.backend()
            if self.mode == "call":
                self.stop_call(backend)
            # Disconnect only the previously selected peer, preserving pairing.
            if self.peer_path:
                try:
                    backend.dbus.Interface(backend.bus.get_object("org.bluez", self.peer_path),
                                           "org.bluez.Device1").Disconnect(timeout=5)
                except backend.dbus.DBusException as exc:
                    if exc.get_dbus_name() not in ("org.bluez.Error.NotConnected", "org.bluez.Error.DoesNotExist"):
                        raise AudioError("bluetooth_stop_failed") from exc
            self.mode = "wifi"
            self.call_until = 0
            self.playback_sink = None
            self.call_error = None
        finally:
            if backend:
                backend.close()
            self.lock.release()

    def finish_wifi(self):
        with self.lock:
            if self.mode == "wifi":
                self.mode = "playback"

    def levels(self):
        return self.volume_operation()

    def set_level(self, kind, db, muted):
        if kind not in ("output", "input") or type(db) not in (int, float) or not math.isfinite(db) or not -60 <= db <= (20 if kind == "input" else 0) or type(muted) is not bool:
            raise AudioError("invalid_level")
        return self.volume_operation(kind, db, muted)

    def volume_operation(self, kind=None, db=None, muted=None):
        if not self.lock.acquire(timeout=1 if self.quiet else 0):
            raise AudioError("audio_busy")
        backend = None
        try:
            backend = self.backend()
            if self.quiet:
                try:
                    self.quiet.adopt(backend.audio_device("output"))
                except AudioError as exc:
                    if not str(exc).startswith(("usb_output_", "headphone_output_")):
                        raise
                if kind == "output":
                    result = self.quiet.manual_level(backend, db, muted)
                else:
                    result = backend.levels() if kind is None else backend.set_level(kind, db, muted)
                return self.quiet.decorate(result)
            return backend.levels() if kind is None else backend.set_level(kind, db, muted)
        finally:
            if backend is not None:
                backend.close()
            self.lock.release()

    def connect(self, value, mode="playback"):
        mac = address(value)
        if mode not in ("playback", "call"):
            raise AudioError("invalid_mode")
        if not self.lock.acquire(timeout=1 if self.quiet else 0):
            raise AudioError("audio_busy")
        backend = None
        own_address = None
        call_profile_attempted = False
        try:
            if self.mode == "call" and self.peer != mac:
                raise AudioError("audio_peer_in_use")
            backend = self.backend()
            path, adapter = backend.device(mac)
            own_address = backend.adapter_address(adapter)
            backend.ensure_power(adapter)
            if self.quiet:
                self.quiet.tick(backend, active_hint=True)
            if mode == "call":
                backend.audio_device("input")
                backend.audio_device("output")
                self.mode = "call"
                self.peer, self.peer_path, self.adapter = mac, path, own_address
                call_profile_attempted = True
                backend.connect_profile(path, "call")
                backend.prepare_call(mac)
                with self.lease_lock:
                    self.call_until = self.clock() + 30
                self.call_error = None
                for attempt in range(4):
                    self.call_state = backend.reconcile_call(mac)
                    if self.call_state.get("microphoneReady") and self.call_state.get("speakerReady"):
                        break
                    if attempt != 3:
                        time.sleep(0.2)
                if self.quiet:
                    self.quiet.tick(backend, active_hint=bool(self.call_state.get("speakerReady")))
                return self.connection_state()
            try:
                backend.remove_call_routes(mac)
            finally:
                backend.disconnect_call_profile(path)
            self.mode = "playback"
            with self.lease_lock:
                self.call_until = 0
            self.call_state = {}
            self.call_error = None
            self.peer, self.peer_path, self.adapter = mac, path, own_address
            backend.connect_profile(path)
            sink = backend.route(mac)
            self.playback_sink = sink
            return self.connection_state()
        except Exception as failure:
            exc = failure if isinstance(failure, AudioError) else AudioError("audio_service_unavailable", f"{type(failure).__name__}: {str(failure)}")
            if mode == "call" and own_address:
                self.call_error = str(exc)
                if call_profile_attempted or (self.mode == "call" and self.peer == mac):
                    try:
                        self.stop_call(backend, str(exc))
                    except Exception as cleanup:
                        self.call_error = "call_stop_failed"
                        exc = AudioError("call_stop_failed", str(cleanup))
                return {"ok": False, "mode": "call", "adapterAddress": own_address,
                        "callRequested": False, "microphoneReady": False, "speakerReady": False,
                        "error": str(exc), "detail": exc.detail}
            raise exc
        finally:
            if backend is not None:
                backend.close()
            self.lock.release()

    def tick(self):
        if (not self.quiet and self.mode != "call") or not self.lock.acquire(blocking=False):
            return
        backend = None
        try:
            backend = self.backend()
            if self.mode == "call":
                if self.call_until <= self.clock():
                    self.stop_call(backend, "call_expired")
                else:
                    try:
                        self.call_state = backend.reconcile_call(self.peer)
                        self.call_error = None
                    except AudioError as exc:
                        self.stop_call(backend, str(exc))
            if self.quiet:
                self.quiet.tick(backend, time.monotonic() < self.active_hint_until or
                                (self.mode == "call" and self.call_until > self.clock() and self.call_state.get("speakerReady", False)))
        except Exception as exc:
            if self.mode == "call":
                self.call_error = str(exc)[:120]
                self.call_state = {"microphoneReady": False, "speakerReady": False}
            if self.quiet:
                self.quiet.error = str(exc)[:120]
            # If activity detection fails, do not leave playback automatically
            # muted. The user's explicit mute remains in force.
            if backend is not None and self.quiet:
                try:
                    self.quiet.release(backend)
                except Exception:
                    pass
        finally:
            if backend is not None:
                backend.close()
            self.lock.release()

    def run(self):
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        import dbus
        bus = None
        loop = GLib.MainLoop()
        watcher = None
        try:
            bus = dbus.SystemBus(private=True, mainloop=DBusGMainLoop())
            def changed(interface, properties, invalidated):
                if interface == "org.bluez.MediaTransport1":
                    if properties.get("State") in ("pending", "active"):
                        self.active_hint_until = time.monotonic() + 1
                    self.wake.set()
            bus.add_signal_receiver(changed, signal_name="PropertiesChanged",
                                    dbus_interface="org.freedesktop.DBus.Properties", bus_name="org.bluez")
            watcher = threading.Thread(target=loop.run, daemon=True)
            watcher.start()
        except Exception:
            # Periodic checks also cover local playback and USB hotplug.
            pass
        try:
            while not self.closed.is_set():
                self.tick()
                self.wake.wait(0.5)
                self.wake.clear()
        finally:
            loop.quit()
            if watcher:
                watcher.join(timeout=2)
            if bus:
                bus.close()
            with self.lock:
                backend = None
                try:
                    backend = self.backend()
                    if self.mode == "call":
                        self.stop_call(backend)
                    if self.quiet:
                        self.quiet.release(backend)
                except Exception:
                    pass
                finally:
                    if backend:
                        backend.close()
