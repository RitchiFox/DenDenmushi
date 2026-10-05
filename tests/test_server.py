import importlib.util
import io
import sys
import json
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
from audio import AudioError
from servos import Servos

spec = importlib.util.spec_from_file_location("server", Path(__file__).parents[1] / "pi/server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class FakeProcess:
    def __init__(self):
        self.stdout = io.BytesIO()
        self.code = None
    def poll(self): return self.code
    def terminate(self): self.code = -15
    def kill(self): self.code = -9
    def wait(self, timeout=None): return self.code


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def spawn(args, **kwargs):
            self.calls.append(args)
            return FakeProcess()
        self.camera = server.Camera(popen=spawn)
    def tearDown(self): self.camera.stop()
    def test_idle_never_opens_camera(self):
        self.camera.tick()
        self.assertEqual(self.calls, [])
    def test_reconnect_reaps_old_processes_and_restarts_pair(self):
        self.camera.start(); self.camera.tick()
        old = list(self.camera.processes)
        old[1].code = 1
        self.camera.tick()
        self.assertEqual(old[0].code, -15)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(self.camera.status()["processesRunning"])
        self.assertEqual(self.calls[1][-1], "tcp://127.0.0.1:8554?listen=1")
    def test_expired_client_lease_stops_capture(self):
        self.camera.start(); self.camera.tick()
        old = list(self.camera.processes)
        self.camera.last_seen = time.monotonic() - 31
        self.camera.tick()
        self.assertFalse(self.camera.status()["requested"])
        self.assertTrue(all(p.poll() is not None for p in old))
    def test_mux_start_failure_cleans_capture(self):
        p = FakeProcess()
        calls = 0
        def spawn(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError("missing ffmpeg")
            return p
        self.camera.popen = spawn
        self.camera.start(); self.camera.tick()
        self.assertEqual(p.code, -15)
        self.assertEqual(self.camera.status()["error"], "missing ffmpeg")
        self.assertFalse(self.camera.status()["processesRunning"])
    def test_status_does_not_renew_camera_lease(self):
        self.camera.start()
        old = self.camera.last_seen
        self.camera.status()
        self.assertEqual(old, self.camera.last_seen)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.camera = server.Camera()
        self.audio_calls = []
        test = self
        class FakeAudio:
            def levels(self): return {"ok": True, "output": {"available": True, "db": -10, "muted": False}}
            def set_level(self, kind, db, muted):
                test.audio_calls.append((kind, db, muted))
                return self.levels()
            def connect(self, address, mode='playback'):
                test.audio_calls.append(address if mode=='playback' else (address,mode))
                if address == "unpaired": raise AudioError("pair_required")
                return {"ok": True, "adapterAddress": "AA:BB:CC:DD:EE:FF"}
            def status(self): return {'mode':'call','microphoneReady':False,'callRequested':True}
            def heartbeat(self, microphone=False): test.audio_calls.append(('heartbeat',microphone))
        self.servo_calls = []
        class FakeServoDriver:
            def pulse(self, pin, width): test.servo_calls.append((pin, width))
            def tick(self): pass
            def extend(self, pin, width): test.servo_calls.append(("extend", pin, width))
        self.servos = Servos(FakeServoDriver())
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(self.camera, FakeAudio(), self.servos))
        self.thread = threading.Thread(target=self.http.serve_forever)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.http.server_port
    def tearDown(self):
        self.http.shutdown(); self.thread.join(); self.http.server_close()
    def request(self, path, method="GET", headers=None, data=None):
        req = urllib.request.Request(self.base + path, method=method, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)
    def test_browser_and_missing_header_cannot_start_capture(self):
        for headers in ({}, {"X-DenDen-Client": "desktop-v1", "Origin": "https://example.com"}):
            self.assertEqual(self.request("/camera/start", "POST", headers)[0], 403)
            self.assertFalse(self.camera.desired)
    def test_native_start_stop_and_status(self):
        h = {"X-DenDen-Client": "desktop-v1"}
        self.assertEqual(self.request("/status", headers=h)[1]["service"], "denden")
        self.assertEqual(self.request("/camera/start", "POST", h)[0], 200)
        self.assertTrue(self.camera.desired)
        self.request("/camera/stop", "POST", h)
        self.assertFalse(self.camera.desired)
        self.assertEqual(self.request("/unknown", headers=h)[0], 404)

    def test_audio_native_request_and_capability(self):
        h = {"X-DenDen-Client": "desktop-v1"}
        self.assertIn("audio-connect-v1", self.request("/status", headers=h)[1]["features"])
        status, body = self.request("/audio/connect", "POST", h, b'{"address":"11:22:33:44:55:66"}')
        self.assertEqual(status, 200); self.assertTrue(body["ok"])
        self.assertEqual(self.audio_calls,["11:22:33:44:55:66"])
        self.assertFalse(self.camera.desired)
    def test_audio_rejects_browser_and_unbounded_or_extra_fields(self):
        h = {"X-DenDen-Client": "desktop-v1"}
        self.assertEqual(self.request("/audio/connect", "POST", {}, b'{}')[0],403)
        self.assertEqual(self.request("/audio/connect", "POST", dict(h, Origin="https://example.com"), b'{}')[0],403)
        for data in [b'{}', b'[]', b'x', b'x'*513, b'{"address":"ok","command":"reboot"}']:
            self.assertEqual(self.request("/audio/connect", "POST", h, data)[0],400)
        self.assertEqual(self.audio_calls,[])
    def test_audio_failure_does_not_stop_camera(self):
        h = {"X-DenDen-Client": "desktop-v1"}
        self.request("/camera/start","POST",h)
        _, body=self.request("/audio/connect","POST",h,b'{"address":"unpaired"}')
        self.assertEqual(body,{"ok":False,"error":"pair_required","detail":""})
        self.assertTrue(self.camera.desired)

    def test_levels_require_native_client_and_do_not_start_camera(self):
        h={"X-DenDen-Client":"desktop-v1"}
        self.assertEqual(self.request('/audio/levels')[0],403)
        self.assertEqual(self.request('/audio/levels',headers=dict(h,Origin='https://example.com'))[0],403)
        self.assertIn('audio-levels-v1',self.request('/status',headers=h)[1]['features'])
        self.assertEqual(self.request('/audio/levels',headers=h)[1]['output']['db'],-10)
        self.assertFalse(self.camera.desired)

    def test_level_write_has_bounded_schema_and_no_browser_access(self):
        h={"X-DenDen-Client":"desktop-v1"}; payload=b'{"kind":"output","db":-10,"muted":false}'
        self.assertEqual(self.request('/audio/level','POST',{},payload)[0],403)
        self.assertEqual(self.request('/audio/level','POST',dict(h,Origin='https://example.com'),payload)[0],403)
        for data in [b'{}',b'[]',b'x'*513,b'{"kind":"output","db":0,"muted":false,"command":"reboot"}']:
            self.assertEqual(self.request('/audio/level','POST',h,data)[0],400)
        self.assertTrue(self.request('/audio/level','POST',h,payload)[1]['ok'])
        self.assertEqual(self.audio_calls,[('output',-10,False)])

    def test_call_mode_and_status_are_native_scoped_without_capture(self):
        h={'X-DenDen-Client':'desktop-v1'}
        self.assertIn('audio-call-v1',self.request('/status',headers=h)[1]['features'])
        self.assertIn('audio-mic-gain-v1',self.request('/status',headers=h)[1]['features'])
        self.assertEqual(self.request('/audio/status')[0],403)
        self.assertTrue(self.request('/audio/status',headers=h)[1]['callRequested'])
        self.assertEqual(self.audio_calls,[])
        self.request('/audio/connect','POST',h,b'{"address":"11:22:33:44:55:66","mode":"call"}')
        self.assertEqual(self.audio_calls,[('11:22:33:44:55:66','call')])
        self.assertFalse(self.camera.desired)
        self.assertEqual(self.request('/audio/connect','POST',h,b'{"address":"ok","mode":"shell"}')[0],400)

    def test_microphone_heartbeat_requires_explicit_boolean(self):
        h={'X-DenDen-Client':'desktop-v1'}
        for data in [b'{"microphone":true}',b'{"microphone":false}',b'',b'{}']:
            self.assertEqual(self.request('/heartbeat','POST',h,data)[0],200)
        self.assertEqual(self.audio_calls,[('heartbeat',True),('heartbeat',False),('heartbeat',False),('heartbeat',False)])
        self.audio_calls.clear()
        for data in [b'{"microphone":1}',b'{"microphone":"true"}',b'[]',b'{"command":"capture"}',b'x'*513]:
            self.assertEqual(self.request('/heartbeat','POST',h,data)[0],400)
        self.assertEqual(self.audio_calls,[])

    def test_servo_control_is_independent_of_camera_and_audio(self):
        h = {'X-DenDen-Client': 'desktop-v1'}
        self.assertIn('servos-v1', self.request('/status', headers=h)[1]['features'])
        self.assertTrue(self.request('/servos/status', headers=h)[1]['ok'])
        self.assertEqual(self.servo_calls, [])
        self.request('/servos/command', 'POST', h, b'{"action":"move","channel":2,"angle":90}')
        self.assertEqual(self.servo_calls, [(27, 1500)])
        self.request('/servos/command', 'POST', h, b'{"action":"stop"}')
        self.assertEqual(self.servo_calls[-1], (27, 0))
        self.assertFalse(self.camera.desired)
        self.assertEqual(self.audio_calls, [])

    def test_native_sweep_accepts_one_bounded_trajectory_without_camera_changes(self):
        h = {'X-DenDen-Client': 'desktop-v1'}
        self.servos.driver.name = 'kernel-pwm'
        self.assertIn('servos-sweep-v1', self.request('/status', headers=h)[1]['features'])
        self.request('/servos/command', 'POST', h, b'{"action":"move","channel":1,"angle":45,"wide":true}')
        self.request('/servos/command', 'POST', h, b'{"action":"release","channel":1}')
        self.servo_calls.clear()
        command = {'action': 'sweep', 'channel': 1, 'startAngle': 45, 'endAngle': 90,
                   'durationSeconds': 5, 'wide': True,
                   'lease': '6e552c68-1943-4e06-8203-caa37ae35573'}
        code, state = self.request('/servos/command', 'POST', h, json.dumps(command).encode())
        self.assertEqual(code, 200)
        self.assertTrue(state['channels'][0]['moving'])
        self.assertTrue(state['channels'][0]['holding'])
        self.assertFalse(any(c['active'] for c in state['channels'][1:]))
        self.assertEqual(self.servo_calls, [(17, 1000)])
        invalid = dict(command, durationSeconds=True)
        self.assertEqual(self.request('/servos/command', 'POST', h, json.dumps(invalid).encode())[0], 400)
        self.request('/servos/command', 'POST', h, b'{"action":"release","channel":1}')
        self.assertFalse(self.camera.desired)
        self.assertEqual(self.audio_calls, [])

    def test_servo_schema_bounds_and_browser_access(self):
        h = {'X-DenDen-Client': 'desktop-v1'}
        payload = b'{"action":"move","channel":1,"angle":90}'
        for headers in ({}, dict(h, Origin='https://example.com')):
            self.assertEqual(self.request('/servos/status', headers=headers)[0], 403)
            self.assertEqual(self.request('/servos/command', 'POST', headers, payload)[0], 403)
        for data in [b'x'*513, b'[]', b'{}', b'{"action":"stop","gpio":17}',
                     b'{"action":"move","channel":9,"angle":90}',
                     b'{"action":"move","channel":true,"angle":90}',
                     b'{"action":"move","channel":1,"angle":NaN}',
                     b'{"action":"move","channel":1,"angle":180}']:
            self.assertEqual(self.request('/servos/command', 'POST', h, data)[0], 400)
        self.assertEqual(self.servo_calls, [])


if __name__ == "__main__": unittest.main()
