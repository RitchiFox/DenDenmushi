"""Pairing policy and authenticated HTTP tests; never touch Bluetooth hardware."""
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).parents[1] / 'pi'))
from pairing import Window, PairingClient, SECONDS
from server import Camera, handler_for


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.window = Window(clock=lambda: self.now)

    def test_starts_closed_and_expires(self):
        with self.assertRaises(PermissionError): self.window.accept('/peer')
        self.window.open()
        self.assertEqual(self.window.status()['secondsRemaining'], SECONDS)
        self.now += SECONDS
        self.assertFalse(self.window.status()['active'])
        with self.assertRaises(PermissionError): self.window.accept('/peer')

    def test_only_one_candidate_can_be_paired_and_trusted(self):
        self.window.open()
        self.assertFalse(self.window.paired('/old-peer'))
        self.window.accept('/new-peer')
        with self.assertRaises(PermissionError): self.window.accept('/other-peer')
        self.assertTrue(self.window.paired('/new-peer'))
        self.assertFalse(self.window.paired('/other-peer'))
        self.window.close()
        self.assertFalse(self.window.paired('/new-peer'))

    def test_reopen_resets_candidate_and_duration(self):
        self.window.open(); self.window.accept('/first')
        self.now += 170
        self.window.open(); self.window.accept('/second')
        self.assertEqual(self.window.status()['secondsRemaining'], SECONDS)
        self.assertFalse(self.window.paired('/first'))

    def test_client_rejects_arbitrary_commands_before_connecting(self):
        with self.assertRaises(ValueError): PairingClient().command('shell')


class HTTPPairingTests(unittest.TestCase):
    def setUp(self):
        self.camera = Camera()
        self.calls = []
        self.window = Window()
        self.wifi_active = False
        self.fail = False
        test = self
        class Pairing:
            def command(self, action):
                test.calls.append(action)
                if test.fail: raise OSError('offline')
                if action == 'open': test.window.open()
                if action == 'close': test.window.close()
                return test.window.status()
        class WiFi:
            def status(self): return {'active': test.wifi_active}
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.camera, wifi=WiFi(), pairing=Pairing()))
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown(); self.thread.join(); self.http.server_close()

    def request(self, path='/bluetooth/pairing/open', headers=None, data=b''):
        if headers is None: headers = {'X-DenDen-Client': 'desktop-v1'}
        request = urllib.request.Request(self.base + path, headers=headers, data=data)
        try:
            with urllib.request.urlopen(request, timeout=2) as r: return r.status, json.load(r)
        except urllib.error.HTTPError as e: return e.code, json.load(e)

    def test_browser_and_unauthorized_cannot_enable_pairing(self):
        for h in ({}, {'X-DenDen-Client': 'desktop-v1', 'Origin': 'https://example.com'}):
            self.assertEqual(self.request(headers=h)[0], 403)
        self.assertEqual(self.calls, [])

    def test_idle_open_status_and_new_camera_connection_closes_window(self):
        self.assertTrue(self.request()[1]['active'])
        self.assertTrue(self.request('/bluetooth/pairing', data=None)[1]['active'])
        self.assertEqual(self.request('/camera/start')[0], 200)
        self.assertFalse(self.window.status()['active'])

    def test_busy_camera_or_wifi_prevents_pairing(self):
        self.camera.start()
        self.assertEqual(self.request()[0], 409)
        self.camera.stop(); self.wifi_active = True
        self.assertEqual(self.request()[0], 409)
        self.assertEqual(self.calls, [])

    def test_invalid_body_does_not_open_window(self):
        self.assertEqual(self.request(data=b'[]')[0], 400)
        self.assertEqual(self.request(data=b'{"seconds":0}')[0], 400)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.request(data=b'{}')[1]['active'])

    def test_unavailable_pairing_is_reported_without_breaking_camera(self):
        self.fail = True
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.request('/camera/start')[0], 200)
        self.assertTrue(self.camera.desired)
