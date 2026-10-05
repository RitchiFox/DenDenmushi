"""Protocol/lifecycle checks without accessing microphones or speakers."""
import io
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).parents[1] / 'pi'))
from wifi_audio import BLOCK, read_block, Session, WiFiAudio
from audio import AudioError
from server import Camera, handler_for


class ShortReader:
    def __init__(self, value): self.buffer = io.BytesIO(value)
    def read(self, size): return self.buffer.read(min(size, 7))


class Process:
    def __init__(self, args, **kw):
        self.args = args; self.stdin = io.BytesIO(); self.stdout = io.BytesIO(bytes(BLOCK * 5))
        self.code = None; self.terminated = False
    def poll(self): return self.code
    def terminate(self): self.terminated = True; self.code = -15
    def kill(self): self.code = -9
    def wait(self, timeout=None): return self.code


class WiFiAudioTests(unittest.TestCase):
    def test_fragmentation_preserves_byte_order(self):
        data = bytes(range(256)) * 15
        self.assertEqual(read_block(ShortReader(data)), data)
    def test_partial_eof_never_emits_a_bad_frame(self):
        with self.assertRaises(EOFError): read_block(io.BytesIO(b'\0' * (BLOCK - 1)))
    def test_mic_off_never_spawns_capture(self):
        calls = []
        def spawn(args, **kw):
            p = Process(args, **kw); calls.append(p); return p
        session = Session(Mock(), False, spawn)
        session.start('usb sink', None, {})
        self.assertEqual(len(calls), 1)
        self.assertIn('--device=usb sink', calls[0].args)
        session.close(); session.close()
        self.assertTrue(calls[0].terminated)
    def test_capture_start_failure_cleans_playback(self):
        calls = []
        def spawn(args, **kw):
            if calls: raise OSError('missing capture')
            p = Process(args, **kw); calls.append(p); return p
        session = Session(Mock(), True, spawn)
        with self.assertRaises(OSError): session.start('sink', 'source', {})
        session.close()
        self.assertTrue(calls[0].terminated)
    def test_stopped_session_never_restarts(self):
        spawn = Mock(); session = Session(Mock(), False, spawn)
        session.close()
        with self.assertRaises(AudioError): session.start('sink', None, {})
        spawn.assert_not_called()
    def test_missing_endpoint_preserves_bluetooth_and_http_error_response(self):
        audio = Mock(); audio.backend.return_value.audio_device.side_effect = AudioError('usb_output_missing')
        wifi = WiFiAudio(audio)
        handler = Mock()
        with self.assertRaises(AudioError): wifi.serve(handler, False)
        audio.suspend_for_wifi.assert_not_called()
        handler.send_response.assert_not_called()
        handler.connection.shutdown.assert_not_called()
        self.assertIsNone(wifi.session)
    def test_second_client_rejected_without_replacing_first(self):
        wifi = WiFiAudio(Mock()); sentinel = object(); wifi.session = sentinel
        with self.assertRaisesRegex(AudioError, 'audio_busy'): wifi.serve(Mock(), True)
        self.assertIs(wifi.session, sentinel)
    def test_idle_status_does_not_open_devices(self):
        audio = Mock(); wifi = WiFiAudio(audio)
        self.assertFalse(wifi.status()['active'])
        audio.backend.assert_not_called()
    def test_eof_reaps_both_directions(self):
        calls = []
        def spawn(args, **kw):
            p = Process(args, **kw); calls.append(p); return p
        session = Session(Mock(), True, spawn)
        session.start('sink', 'source', {})
        session.run(io.BytesIO())
        self.assertTrue(session.closed.is_set())
        self.assertTrue(all(p.terminated for p in calls))


class HTTPWiFiTests(unittest.TestCase):
    def setUp(self):
        self.wifi = Mock()
        self.wifi.status.return_value = {'ok': True, 'active': False}
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(Camera(), wifi=self.wifi))
        self.thread = threading.Thread(target=self.http.serve_forever); self.thread.start()
    def tearDown(self):
        self.http.shutdown(); self.thread.join(); self.http.server_close()
    def request(self, path, headers=''):
        with socket.create_connection(self.http.server_address, 2) as sock:
            sock.sendall(('GET ' + path + ' HTTP/1.0\r\n' + headers + '\r\n').encode())
            return sock.recv(4096)
    def test_no_header_and_browser_origin_rejected(self):
        for h in ['', 'X-DenDen-Client: desktop-v1\r\nOrigin: https://example.com\r\n']:
            self.assertIn(b'403', self.request('/audio/wifi/stream', h))
        self.wifi.serve.assert_not_called()
    def test_invalid_upgrade_does_not_open_microphone(self):
        self.assertIn(b'400', self.request('/audio/wifi/stream', 'X-DenDen-Client: desktop-v1\r\nX-DenDen-Microphone: yes\r\n'))
        self.wifi.serve.assert_not_called()
    def test_capability_advertised(self):
        self.request('/status', 'X-DenDen-Client: desktop-v1\r\n')
        self.wifi.serve.assert_not_called()


if __name__ == '__main__': unittest.main()
