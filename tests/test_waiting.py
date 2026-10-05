import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parents[1] / 'pi'))
from waiting import WaitingSound

class Process:
    def __init__(self): self.code=None
    def poll(self): return self.code
    def terminate(self): self.code=0
    def wait(self, timeout): return self.code
    def kill(self): self.code=-9

class WaitingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now=0; self.bt=False; self.camera=False; self.wifi=False; self.calls=[]
        backend=SimpleNamespace(objects=lambda:{'peer':{'org.bluez.Device1':{'Connected':self.bt}}},
            audio_device=lambda kind:{'name':'usb'}, env={}, close=lambda:None)
        audio=SimpleNamespace(lock=threading.Lock(),backend=lambda:backend)
        self.w=WaitingSound(audio,SimpleNamespace(status=lambda:{'requested':self.camera}),
            SimpleNamespace(status=lambda:{'active':self.wifi}),
            path=str(Path(self.tmp.name)/'waiting.json'),clock=lambda:self.now,popen=self.spawn)
    def spawn(self,args,**kwargs):
        self.calls.append(args); return Process()
    def test_boot_loop_and_connection_stop(self):
        self.w.tick(); self.assertEqual(self.calls,[])
        self.now=11; self.w.tick(); self.assertTrue(self.w.status()['playing'])
        self.assertIn('--volume=13107',self.calls[0])
        self.w.process.code=0; self.w.tick(); self.assertEqual(len(self.calls),2)
        old=self.w.process;self.w.connected();self.assertEqual(old.code,0)
        self.now=40;self.w.tick();self.assertFalse(self.w.status()['playing'])
        self.now=47;self.w.tick();self.assertTrue(self.w.status()['playing'])
    def test_camera_wifi_bluetooth_all_suppress(self):
        self.now=11
        for attr in ('camera','wifi','bt'):
            setattr(self,attr,True);self.w.tick();self.assertFalse(self.w.status()['playing'])
            setattr(self,attr,False);self.w.tick();self.assertTrue(self.w.status()['playing'])
        self.w.closed.set();self.w.tick();self.assertFalse(self.w.status()['playing'])
    def test_volume_persistence_and_disable(self):
        self.now=11;self.w.tick()
        self.w.configure({'enabled':True,'volume':5})
        self.w.tick();self.assertIn('--volume=3277',self.calls[-1])
        self.w.configure({'enabled':False,'volume':5});self.w.tick()
        self.assertFalse(self.w.status()['playing'])
        import json
        self.assertEqual(json.loads(self.w.path.read_text()),{'enabled':False,'volume':5})
    def test_invalid_settings_do_not_write_or_stop(self):
        for volume in (-1,101,True,'20',float('nan')):
            with self.assertRaises(ValueError):self.w.configure({'enabled':True,'volume':volume})
        with self.assertRaises(ValueError):self.w.configure({'enabled':1,'volume':20})
        with self.assertRaises(ValueError):self.w.configure({'enabled':True,'volume':20,'url':'other'})
        self.assertFalse(self.w.path.exists())
