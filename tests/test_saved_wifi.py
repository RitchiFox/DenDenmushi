"""Saved-network switching uses existing credentials and rolls back failures."""
import pathlib
import sys
import types
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'pi'))
from provision_backend import Backend

UUID = '11111111-1111-4111-8111-111111111111'

class FakeBus:
    def __init__(self, state=2):
        self.state = state
        self.calls = []
        self.closed = False
    def get_object(self, service, path):
        bus = self
        class Object:
            def Get(self, interface, key):
                return {'ActiveConnections': ['/active'], 'Uuid': UUID, 'DeviceType': 2, 'State': bus.state}[key]
            def GetDevices(self): return ['/wifi']
            def ListConnections(self): return ['/profile']
            def GetSettings(self):
                return {'connection': {'uuid': UUID, 'type': '802-11-wireless'},
                        '802-11-wireless': {'ssid': b'home'},
                        '802-11-wireless-security': {'psk': 'must-never-leak'}}
            def CheckpointCreate(self, *args): bus.calls.append('checkpoint'); return '/checkpoint'
            def CheckpointDestroy(self, *args): bus.calls.append('destroy')
            def CheckpointRollback(self, *args): bus.calls.append('rollback')
            def ActivateConnection(self, *args, **kwargs): bus.calls.append(('activate', args)); return '/active'
        return Object()
    def close(self): self.closed = True

class SavedWiFiTests(unittest.TestCase):
    def backend(self, bus):
        fake = types.SimpleNamespace(SystemBus=lambda **kw: bus, Interface=lambda obj, interface: obj,
                                     UInt32=int, ObjectPath=str)
        backend = Backend.__new__(Backend)
        backend.info = lambda: {'host': 'pi.local'}
        return backend, patch.dict(sys.modules, dbus=fake)
    def test_listing_does_not_expose_passwords(self):
        bus = FakeBus(); backend, context = self.backend(bus)
        with context: result = backend.saved_wifi()
        self.assertEqual(result, {'networks': [{'id': UUID, 'ssid': 'home', 'active': True}], 'next': None})
        self.assertTrue(bus.closed)
    def test_saved_activation_no_new_credentials(self):
        bus = FakeBus(); backend, context = self.backend(bus)
        with context: result = backend({'op': 'wifi-use', 'id': 'protocol-correlation', 'uuid': UUID})
        self.assertEqual(result['host'], 'pi.local')
        self.assertEqual(bus.calls, ['checkpoint', ('activate', ('/profile', '/wifi', '/')), 'destroy'])
        self.assertTrue(bus.closed)
    def test_failed_activation_restores_previous_connection(self):
        bus = FakeBus(4); backend, context = self.backend(bus)
        with context, self.assertRaises(ValueError): backend.use_wifi(UUID)
        self.assertEqual(bus.calls[-2:], ['rollback', 'destroy'])
        self.assertTrue(bus.closed)
    def test_unknown_profile_does_not_change_network(self):
        bus = FakeBus(); backend, context = self.backend(bus)
        with context, self.assertRaises(ValueError): backend.use_wifi('22222222-2222-4222-8222-222222222222')
        self.assertEqual(bus.calls, [])
        self.assertTrue(bus.closed)
    def test_bad_offsets_rejected(self):
        bus = FakeBus(); backend, context = self.backend(bus)
        with context, self.assertRaises(ValueError): backend.saved_wifi(-1)

if __name__ == '__main__': unittest.main()
