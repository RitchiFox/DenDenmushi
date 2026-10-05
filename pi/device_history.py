"""Local connection observations; device names are self-reported, not identities."""
import base64
import json
import os
import re
import threading
import time
from pathlib import Path


class DeviceHistory:
    def __init__(self, path='/var/lib/denden-audio/devices.json', clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.error = None
        self.live = {}
        self.saved = 0
        self.records = {}
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('devices'), dict):
                raise ValueError('invalid history')
            self.records = data['devices']
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.error = 'history_unavailable'

    def _save(self, force=False):
        if self.error or (not force and self.clock() - self.saved < 30):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            with temp.open('w') as output:
                os.chmod(temp, 0o600)
                json.dump({'version': 1, 'devices': self.records}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
            self.saved = self.clock()
        except OSError:
            self.error = 'history_unavailable'

    def observe(self, key, name, transport, connected, saved_only=False):
        with self.lock:
            if self.error:
                return
            now = self.clock()
            fresh = key not in self.records
            record = self.records.setdefault(key, dict(id=key, name=name, transport=transport,
                                                       firstSeen=None, lastSeen=None))
            first_connection = connected and record['firstSeen'] is None
            record['name'] = name[:120]
            if connected:
                if record['firstSeen'] is None:
                    record['firstSeen'] = now
                record['lastSeen'] = now
                self.live[key] = now
            elif not saved_only:
                self.live.pop(key, None)
            self._save(force=fresh or first_connection)

    def desktop(self, identifier, encoded_name):
        if not identifier or not re.fullmatch(r'[0-9a-fA-F-]{36}', identifier):
            return
        try:
            if not encoded_name or len(encoded_name) > 640:
                return
            name = base64.b64decode(encoded_name, validate=True).decode('utf-8')
            if not name.strip() or len(name) > 120 or any(ord(c) < 32 for c in name):
                return
        except (ValueError, UnicodeError):
            return
        self.observe('app:' + identifier.lower(), name, 'app', True)

    def bluetooth(self):
        from audio import Backend
        backend = Backend()
        try:
            for interfaces in backend.objects().values():
                props = interfaces.get('org.bluez.Device1', {})
                mac = str(props.get('Address', '')).upper()
                if not re.fullmatch(r'[0-9A-F]{2}(:[0-9A-F]{2}){5}', mac):
                    continue
                connected = bool(props.get('Connected'))
                if not connected and not props.get('Paired'):
                    continue
                self.observe('bt:' + mac, str(props.get('Alias') or props.get('Name') or mac),
                             'bluetooth', connected)
        finally:
            backend.close()

    def status(self):
        with self.lock:
            now = self.clock()
            devices = [dict(r, connected=now - self.live.get(k, -1e20) < 30)
                       for k, r in self.records.items()]
            return dict(ok=self.error is None, devices=sorted(devices, key=lambda r:r.get('lastSeen') or 0, reverse=True))

    def run(self):
        while not self.closed.is_set():
            try:
                self.bluetooth()
            except Exception:
                pass  # Unavailable Bluetooth must not discard stored history.
            if self.closed.wait(10):
                break
        with self.lock:
            self._save(force=True)
