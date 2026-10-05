"""Bounded classic Bluetooth pairing; app access still needs the BLE device code.

The root setup service owns the BlueZ agent. Only root or the configured Pi
owner can reach its private Unix socket; desktop requests arrive over SSH.
"""
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time

SOCKET = '/run/denden-pairing/control.sock'
SECONDS = 180
AUDIO_UUIDS = {'0000' + short + '-0000-1000-8000-00805f9b34fb'
               for short in ('1108', '1112', '110a', '110b', '110c', '110e', '110f', '111e', '111f')}


class Window:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.until = 0
        self.candidate = None

    def status(self):
        remaining = max(0, self.until - self.clock())
        return {'ok': True, 'active': remaining > 0, 'secondsRemaining': int(remaining + 0.999)}

    def open(self):
        self.candidate = None
        self.until = self.clock() + SECONDS

    def close(self):
        self.until = 0
        self.candidate = None

    def accept(self, path):
        if not self.status()['active'] or (self.candidate is not None and self.candidate != path):
            raise PermissionError('pairing_window_closed')
        self.candidate = path

    def paired(self, path):
        return self.status()['active'] and self.candidate == path


class PairingClient:
    def command(self, action):
        if action not in ('open', 'close', 'status'):
            raise ValueError('invalid_pairing_action')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(4)
            client.connect(SOCKET)
            client.sendall((action + '\n').encode())
            data = bytearray()
            while len(data) < 1024 and not data.endswith(b'\n'):
                part = client.recv(1024 - len(data))
                if not part:
                    break
                data.extend(part)
        value = json.loads(data)
        if not value.get('ok'):
            raise OSError('pairing_unavailable')
        return value


def start_pairing(bus, adapter, user):
    """Called on provision.py's GLib thread; no new daemon or sudo rule."""
    import dbus
    import dbus.service
    from gi.repository import GLib
    import pwd

    props_if = 'org.freedesktop.DBus.Properties'
    adapter_if = 'org.bluez.Adapter1'
    device_if = 'org.bluez.Device1'
    agent_path = '/dev/denden/pairing_agent'
    window = Window()
    manager = dbus.Interface(bus.get_object('org.bluez', '/org/bluez'), 'org.bluez.AgentManager1')
    adapter_props = dbus.Interface(bus.get_object('org.bluez', adapter), props_if)
    registered = False
    radio_open = False

    def device_props(path):
        if not str(path).startswith(str(adapter) + '/dev_'):
            raise PermissionError('wrong_adapter')
        return dbus.Interface(bus.get_object('org.bluez', path), props_if)

    def close():
        nonlocal registered, radio_open
        window.close()  # Reject callbacks even if a BlueZ property write fails.
        failures = []
        for name in ('Pairable', 'Discoverable'):
            try:
                adapter_props.Set(adapter_if, name, dbus.Boolean(False), timeout=2)
            except Exception:
                failures.append(name)
        if registered:
            try:
                manager.UnregisterAgent(agent_path, timeout=2)
                registered = False
            except Exception:
                failures.append('agent')
        radio_open = bool(failures)
        if failures:
            raise OSError('pairing_close_failed')

    def open_window():
        nonlocal registered, radio_open
        close()
        try:
            manager.RegisterAgent(agent_path, 'NoInputNoOutput', timeout=2)
            registered = True
            manager.RequestDefaultAgent(agent_path, timeout=2)
            adapter_props.Set(adapter_if, 'Powered', dbus.Boolean(True), timeout=2)
            # BlueZ itself expires discovery/pairability if this process crashes.
            for name in ('DiscoverableTimeout', 'PairableTimeout'):
                adapter_props.Set(adapter_if, name, dbus.UInt32(SECONDS), timeout=2)
            window.open()
            radio_open = True
            adapter_props.Set(adapter_if, 'Pairable', dbus.Boolean(True), timeout=2)
            adapter_props.Set(adapter_if, 'Discoverable', dbus.Boolean(True), timeout=2)
        except Exception:
            try:
                close()
            except Exception:
                pass
            raise

    class Rejected(dbus.DBusException):
        _dbus_error_name = 'org.bluez.Error.Rejected'

    class Agent(dbus.service.Object):
        def accept(self, path):
            try:
                device_props(path)
                window.accept(str(path))
            except Exception:
                raise Rejected('No active pairing window')

        @dbus.service.method('org.bluez.Agent1', in_signature='o', out_signature='')
        def RequestAuthorization(self, device):
            self.accept(device)

        @dbus.service.method('org.bluez.Agent1', in_signature='ou', out_signature='')
        def RequestConfirmation(self, device, passkey):
            self.accept(device)

        @dbus.service.method('org.bluez.Agent1', in_signature='os', out_signature='')
        def AuthorizeService(self, device, uuid):
            if str(uuid).lower() not in AUDIO_UUIDS:
                raise Rejected('Only audio profiles are supported')
            props = device_props(device)
            if props.Get(device_if, 'Paired') and props.Get(device_if, 'Trusted'):
                return
            self.accept(device)

        @dbus.service.method('org.bluez.Agent1', in_signature='o', out_signature='s')
        def RequestPinCode(self, device):
            raise Rejected('Legacy PIN pairing is unsupported')

        @dbus.service.method('org.bluez.Agent1', in_signature='o', out_signature='u')
        def RequestPasskey(self, device):
            raise Rejected('Use modern Bluetooth pairing')

        @dbus.service.method('org.bluez.Agent1', in_signature='', out_signature='')
        def Cancel(self):
            window.candidate = None

        @dbus.service.method('org.bluez.Agent1', in_signature='', out_signature='')
        def Release(self):
            nonlocal registered
            registered = False
            window.close()

    agent = Agent(bus, agent_path)

    def changed(interface, values, invalidated, path=None):
        if interface == device_if and values.get('Paired') and window.paired(str(path)):
            try:
                # Trust only the single peer accepted during this explicit window.
                device_props(path).Set(device_if, 'Trusted', dbus.Boolean(True), timeout=2)
                close()
            except Exception:
                window.close()

    bus.add_signal_receiver(changed, dbus_interface=props_if, signal_name='PropertiesChanged', path_keyword='path', bus_name='org.bluez')

    def tick():
        if radio_open and not window.status()['active']:
            try:
                close()
            except Exception:
                pass
        return True

    # Root owns the parent; its socket cannot be replaced by the unprivileged user.
    account = pwd.getpwnam(user)
    directory = Path(SOCKET).parent
    directory.mkdir(mode=0o755, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o022:
        raise RuntimeError('Unsafe pairing socket directory')
    # The setup service uses UMask=0077. The owner must traverse this root-owned
    # directory; access to commands is restricted by the 0600 socket below.
    directory.chmod(0o755)
    Path(SOCKET).unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(SOCKET)
    os.chown(SOCKET, account.pw_uid, account.pw_gid)
    os.chmod(SOCKET, 0o600)
    listener.listen(4)
    close()  # Startup never opens a pairing window.
    GLib.timeout_add_seconds(1, tick)

    def listen():
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(4)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                    if uid not in (0, account.pw_uid):
                        raise PermissionError('wrong_peer')
                    data = bytearray()
                    while len(data) < 16 and not data.endswith(b'\n'):
                        part = connection.recv(16 - len(data))
                        if not part:
                            break
                        data.extend(part)
                    action = data.decode().strip()
                    if action not in ('open', 'close', 'status'):
                        raise ValueError('invalid_pairing_action')
                    done = threading.Event()
                    result = {}
                    deadline = time.monotonic() + 3

                    def execute(action=action, done=done, result=result, deadline=deadline):
                        if time.monotonic() > deadline:
                            done.set()
                            return False
                        try:
                            if action == 'open': open_window()
                            elif action == 'close' and radio_open: close()
                            result.update(window.status())
                        except Exception:
                            result.update(ok=False, error='pairing_unavailable')
                        done.set()
                        return False

                    GLib.idle_add(execute)
                    if not done.wait(3.5):
                        raise TimeoutError('pairing_busy')
                    connection.sendall(json.dumps(result or {'ok': False}).encode() + b'\n')
                except Exception:
                    try:
                        connection.sendall(b'{"ok":false,"error":"pairing_unavailable"}\n')
                    except OSError:
                        pass
    threading.Thread(target=listen, daemon=True).start()
    return agent, listener, window
