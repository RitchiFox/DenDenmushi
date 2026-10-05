import json
import os
import pwd
import socket
import subprocess
import time
import uuid
from provision_core import public_key, wifi_values

NM = "org.freedesktop.NetworkManager"
PROPS = "org.freedesktop.DBus.Properties"


class Backend:
    def __init__(self, user):
        self.account = pwd.getpwnam(user)

    def info(self):
        addresses = subprocess.check_output(["/usr/sbin/ip", "-j", "-4", "addr", "show", "scope", "global"], timeout=4)
        ips = [a["local"] for i in json.loads(addresses) for a in i.get("addr_info", []) if a.get("family") == "inet"]
        with open("/etc/ssh/ssh_host_ed25519_key.pub") as f:
            host_key = " ".join(f.read().split()[:2])
        return {"name": "DenDenMushi", "user": self.account.pw_name,
                "host": socket.gethostname().split(".")[0] + ".local", "addresses": ips,
                "hostKey": public_key(host_key), "version": 1}

    def authorize(self, key):
        key = public_key(key)
        # Drop privileges in a child before touching a user's writable directory.
        # A symlink can therefore never make this root service overwrite root files.
        code = r'''
import fcntl, os, pathlib, stat, sys
directory = pathlib.Path.home() / '.ssh'
directory.mkdir(mode=0o700, exist_ok=True)
fd = os.open(directory / 'authorized_keys', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'r+') as f:
    if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise ValueError('Not a file')
    fcntl.flock(f, fcntl.LOCK_EX)
    existing = f.read(1048577)
    if len(existing) > 1048576: raise ValueError('File too large')
    key = sys.stdin.read(256).strip()
    if not any(' '.join(line.split()[-3:-1]) == key for line in existing.splitlines()):
        f.seek(0, 2)
        if existing and not existing.endswith('\n'): f.write('\n')
        f.write('restrict,port-forwarding,command="/bin/false",permitopen="127.0.0.1:8789",permitopen="127.0.0.1:8554" ' + key + ' denden-demo\n')
        f.flush(); os.fsync(f.fileno())
'''
        subprocess.run(["/usr/bin/python3", "-c", code], input=key, text=True, check=True, timeout=8,
                       user=self.account.pw_uid, group=self.account.pw_gid, extra_groups=[],
                       env={"HOME": self.account.pw_dir, "PATH": "/usr/bin:/bin"},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.info()

    def saved_wifi(self, offset=0):
        import dbus
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid network offset")
        bus = dbus.SystemBus(private=True)
        try:
            manager = bus.get_object(NM, "/org/freedesktop/NetworkManager")
            active = dbus.Interface(manager, PROPS).Get(NM, "ActiveConnections")
            active_ids = {str(dbus.Interface(bus.get_object(NM, path), PROPS).Get(NM + ".Connection.Active", "Uuid")) for path in active}
            settings = dbus.Interface(bus.get_object(NM, "/org/freedesktop/NetworkManager/Settings"), NM + ".Settings")
            rows = []
            for path in settings.ListConnections():
                data = dbus.Interface(bus.get_object(NM, path), NM + ".Settings.Connection").GetSettings()
                if data.get("connection", {}).get("type") != "802-11-wireless":
                    continue
                connection = data["connection"]
                identity = str(connection["uuid"])
                rows.append({"id": identity, "ssid": bytes(data["802-11-wireless"]["ssid"]).decode("utf-8", errors="replace"),
                             "active": identity in active_ids})
            rows.sort(key=lambda row: row["id"])
            return {"networks": rows[offset:offset + 10], "next": offset + 10 if offset + 10 < len(rows) else None}
        finally:
            bus.close()

    def use_wifi(self, identity):
        import dbus
        identity = str(uuid.UUID(identity))
        bus = dbus.SystemBus(private=True)
        checkpoint = None
        try:
            manager = bus.get_object(NM, "/org/freedesktop/NetworkManager")
            iface = dbus.Interface(manager, NM)
            settings = dbus.Interface(bus.get_object(NM, "/org/freedesktop/NetworkManager/Settings"), NM + ".Settings")
            profile = None
            for path in settings.ListConnections():
                data = dbus.Interface(bus.get_object(NM, path), NM + ".Settings.Connection").GetSettings()
                if data.get("connection", {}).get("uuid") == identity and data["connection"].get("type") == "802-11-wireless":
                    profile = path
                    break
            if profile is None:
                raise ValueError("Saved Wi-Fi network no longer exists")
            device = next((p for p in iface.GetDevices() if int(dbus.Interface(bus.get_object(NM, p), PROPS).Get(NM + ".Device", "DeviceType")) == 2), None)
            if device is None:
                raise ValueError("Wi-Fi adapter unavailable")
            checkpoint = iface.CheckpointCreate([device], dbus.UInt32(50), dbus.UInt32(0))
            active = iface.ActivateConnection(profile, device, dbus.ObjectPath("/"), timeout=15)
            for _ in range(80):
                state = int(dbus.Interface(bus.get_object(NM, active), PROPS).Get(NM + ".Connection.Active", "State"))
                if state == 2:
                    iface.CheckpointDestroy(checkpoint)
                    checkpoint = None
                    return self.info()
                if state == 4:
                    break
                time.sleep(0.5)
            raise ValueError("Saved Wi-Fi unavailable; restoring previous network")
        finally:
            if checkpoint is not None:
                try:
                    iface.CheckpointRollback(checkpoint)
                    iface.CheckpointDestroy(checkpoint)
                except Exception:
                    pass
            bus.close()

    def wifi(self, ssid, password):
        import dbus
        ssid, password = wifi_values(ssid, password)
        bus = dbus.SystemBus(private=True)
        manager = bus.get_object(NM, "/org/freedesktop/NetworkManager")
        iface = dbus.Interface(manager, NM)
        device_path = next((p for p in iface.GetDevices() if int(dbus.Interface(bus.get_object(NM, p), PROPS).Get(NM + ".Device", "DeviceType")) == 2), None)
        if device_path is None:
            raise ValueError("Pi не знайшов адаптер Wi-Fi у NetworkManager.")
        if bool(dbus.Interface(manager, PROPS).Get(NM, "WirelessHardwareEnabled")) is False:
            raise ValueError("Wi-Fi на Pi апаратно вимкнений.")
        dbus.Interface(manager, PROPS).Set(NM, "WirelessEnabled", dbus.Boolean(True))
        settings = {
            "connection": {"id": "DenDenMushi Wi-Fi", "uuid": str(uuid.uuid4()), "type": "802-11-wireless", "autoconnect": dbus.Boolean(True)},
            "802-11-wireless": {"ssid": dbus.ByteArray(ssid.encode()), "mode": "infrastructure"},
            "802-11-wireless-security": {"key-mgmt": "wpa-psk", "psk": password},
            "ipv4": {"method": "auto"}, "ipv6": {"method": "auto"},
        }
        # A checkpoint restores the previous connection on wrong password/timeout.
        checkpoint = iface.CheckpointCreate([device_path], dbus.UInt32(50), dbus.UInt32(0))
        added = None
        try:
            added, active = iface.AddAndActivateConnection(settings, device_path, dbus.ObjectPath("/"), timeout=15)
            for _ in range(80):
                state = int(dbus.Interface(bus.get_object(NM, active), PROPS).Get(NM + ".Connection.Active", "State"))
                if state == 2:
                    iface.CheckpointDestroy(checkpoint)
                    return self.info()
                if state == 4:
                    break
                time.sleep(0.5)
            raise ValueError("Pi не підключився. Перевір пароль і мережу 2,4 ГГц. Попереднє з’єднання відновлюється.")
        except Exception:
            try:
                iface.CheckpointRollback(checkpoint)
                iface.CheckpointDestroy(checkpoint)
            except Exception:
                pass  # NetworkManager also rolls back when its timeout expires.
            if added:
                try:
                    dbus.Interface(bus.get_object(NM, added), NM + ".Settings.Connection").Delete()
                except Exception:
                    pass
            raise
        finally:
            bus.close()

    def __call__(self, request):
        op = request.get("op")
        if op == "info":
            return self.info()
        if op == "authorize":
            return self.authorize(request.get("publicKey", ""))
        if op == "wifi-list":
            return self.saved_wifi(request.get("offset", 0))
        if op == "wifi-use":
            return self.use_wifi(request.get("uuid"))
        if op == "wifi":
            return self.wifi(request.get("ssid"), request.get("password"))
        raise ValueError("Невідома операція налаштування.")
