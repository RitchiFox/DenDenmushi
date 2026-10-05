import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from audio import Audio, AudioError, Backend
from quiet import QuietOutput


class DAC:
    def __init__(self):
        self.item = {'name': 'alsa_output.usb-Realtek', 'index': 42, 'mute': False}
        self.db = -10
        self.active = False
        self.calls = []

    def audio_device(self, kind): return self.item
    def playback_active(self, item): return self.active
    def pulse(self, *args):
        self.calls.append(args)
        if args[0] == 'set-sink-mute': self.item['mute'] = args[2] == '1'
    def set_level(self, kind, db, muted):
        self.db = db
        self.item['mute'] = muted
        return self.levels()
    def levels(self):
        return {'ok': True, 'output': {'available': True, 'db': self.db, 'muted': self.item['mute']}}
    def close(self): pass


class QuietTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.dac = DAC()
        self.quiet = QuietOutput(clock=lambda: self.now)
        self.audio = Audio(lambda: self.dac, quiet=self.quiet)
    def idle(self):
        self.quiet.tick(self.dac)
        self.now += 3
        self.quiet.tick(self.dac)
    def test_idle_mutes_after_delay_without_changing_gain(self):
        self.quiet.tick(self.dac)
        self.now = 1.9
        self.quiet.tick(self.dac)
        self.assertFalse(self.dac.item['mute'])
        self.now = 2.1
        self.quiet.tick(self.dac)
        self.assertTrue(self.dac.item['mute'])
        self.assertEqual(self.dac.db, -10)
    def test_playback_wakes_automatically_and_manual_mute_survives(self):
        self.idle()
        self.dac.active = True
        self.quiet.tick(self.dac)
        self.assertFalse(self.dac.item['mute'])
        self.audio.set_level('output', -6, True)
        self.dac.active = False
        self.idle()
        self.dac.active = True
        self.quiet.tick(self.dac)
        self.assertTrue(self.dac.item['mute'])
        self.assertEqual(self.dac.db, -6)
    def test_volume_edit_during_idle_preserves_auto_mute_and_ui_choice(self):
        self.idle()
        result = self.audio.set_level('output', -18, False)
        self.assertEqual(result['output']['db'], -18)
        self.assertFalse(result['output']['muted'])
        self.assertTrue(result['output']['effectiveMuted'])
        self.assertTrue(self.audio.levels()['quiet']['active'])
        self.dac.active = True
        self.quiet.tick(self.dac)
        self.assertFalse(self.dac.item['mute'])
        self.assertEqual(self.dac.db, -18)
    def test_brief_pause_does_not_toggle_hardware(self):
        self.dac.active = True
        self.quiet.tick(self.dac)
        self.dac.active = False
        self.now = 1
        self.quiet.tick(self.dac)
        self.dac.active = True
        self.now = 1.5
        self.quiet.tick(self.dac)
        self.assertEqual(self.dac.calls, [])
    def test_pending_transport_hint_wakes_before_next_poll(self):
        self.idle()
        self.quiet.tick(self.dac, active_hint=True)
        self.assertFalse(self.dac.item['mute'])
    def test_restart_recognizes_own_mute_not_a_manual_mute(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'quiet.json'
            self.quiet = QuietOutput(path, clock=lambda: self.now)
            self.idle()
            restarted = QuietOutput(path, clock=lambda: self.now)
            self.dac.active = True
            restarted.tick(self.dac)
            self.assertFalse(self.dac.item['mute'])
            self.assertFalse(json.loads(path.read_text())['automatic'])
    def test_unknown_muted_device_is_not_unmuted(self):
        self.dac.item['mute'] = True
        self.dac.active = True
        self.quiet.tick(self.dac)
        self.assertTrue(self.dac.item['mute'])
        self.quiet.release(self.dac)
        self.assertTrue(self.dac.item['mute'])
    def test_detection_failure_releases_automatic_mute(self):
        self.idle()
        def fail(_): raise AudioError('activity_unavailable')
        self.dac.playback_active = fail
        self.audio.tick()
        self.assertFalse(self.dac.item['mute'])
        self.assertEqual(self.quiet.error, 'activity_unavailable')
    def test_release_restores_only_automatic_mute(self):
        self.idle()
        self.quiet.release(self.dac)
        self.assertFalse(self.dac.item['mute'])
        self.audio.set_level('output', -20, True)
        self.quiet.release(self.dac)
        self.assertTrue(self.dac.item['mute'])


class ActivityTests(unittest.TestCase):
    def backend(self, state='idle', streams=None, modules=None):
        backend = object.__new__(Backend)
        backend.objects = lambda: {'/transport': {'org.bluez.MediaTransport1': {'State': state}}}
        backend.items = lambda kind: streams or [] if kind == 'sink-inputs' else modules or []
        return backend
    def test_pending_and_active_transport_are_playback(self):
        for state in ('pending', 'active'):
            self.assertTrue(self.backend(state).playback_active({'index': 42}))
    def test_idle_bluetooth_direct_stream_does_not_hold_output_open(self):
        stream = {'sink': 42, 'corked': False, 'properties': {'node.name': 'bluez_input.11_22_33_44_55_66.0'}}
        self.assertFalse(self.backend(streams=[stream]).playback_active({'index': 42}))
    def test_idle_bluetooth_loopback_does_not_hold_output_open(self):
        stream = {'sink': 42, 'corked': False, 'owner_module': 99}
        module = {'index': 99, 'name': 'module-loopback', 'argument': 'source=bluez_input.11_22_33_44_55_66.0 sink=usb'}
        self.assertFalse(self.backend(streams=[stream], modules=[module]).playback_active({'index': 42}))
    def test_local_playback_is_preserved_but_other_sink_is_ignored(self):
        stream = {'sink': 42, 'corked': False, 'properties': {'application.name': 'paplay'}}
        self.assertTrue(self.backend(streams=[stream]).playback_active({'index': 42}))
        self.assertFalse(self.backend(streams=[stream]).playback_active({'index': 41}))
        stream['corked'] = True
        self.assertFalse(self.backend(streams=[stream]).playback_active({'index': 42}))


if __name__ == '__main__': unittest.main()
