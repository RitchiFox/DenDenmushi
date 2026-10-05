import base64
import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'pi'))
from device_history import DeviceHistory

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'devices.json';self.now=1000
        self.h=DeviceHistory(self.path,clock=lambda:self.now)
    def test_persist_dedupe_and_offline_after_restart(self):
        id='12345678-1234-1234-1234-123456789abc'
        name=base64.b64encode('Тест Mac'.encode()).decode()
        self.h.desktop(id,name);self.now+=35;self.h.desktop(id,name)
        rows=self.h.status()['devices'];self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['firstSeen'],1000);self.assertEqual(rows[0]['lastSeen'],1035)
        restored=DeviceHistory(self.path,clock=lambda:self.now)
        self.assertFalse(restored.status()['devices'][0]['connected'])
        self.now+=31;self.assertFalse(self.h.status()['devices'][0]['connected'])
    def test_saved_bluetooth_not_invented_date(self):
        self.h.observe('bt:AA','Phone','bluetooth',False)
        row=self.h.status()['devices'][0]
        self.assertIsNone(row['firstSeen']);self.assertIsNone(row['lastSeen'])
        self.h.observe('bt:AA','Phone','bluetooth',True)
        self.assertEqual(self.h.status()['devices'][0]['firstSeen'],1000)
    def test_bad_headers_ignored(self):
        for id,name in [('bad','YQ=='),('a'*36,'%%%%'),('a'*36,base64.b64encode(b'bad\nname').decode())]:
            self.h.desktop(id,name)
        self.assertEqual(self.h.status()['devices'],[])
    def test_corrupt_history_preserved(self):
        self.path.write_text('broken')
        h=DeviceHistory(self.path);h.observe('x','name','app',True)
        self.assertFalse(h.status()['ok']);self.assertEqual(self.path.read_text(),'broken')
