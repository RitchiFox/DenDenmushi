"""AEC routing/lifecycle tests using fake devices only; never load audio modules."""
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'pi'))
from audio import AudioError
from wifi_audio import EchoCancellation, WiFiAudio

class Backend:
    def __init__(self):
        self.calls = []
        self.modules = []
        self.ready = True
        self.fail_load = False
        self.env = {}
    def items(self, kind):
        if kind == 'modules': return self.modules
        if not self.ready: return []
        return [{'name': EchoCancellation.SOURCE if kind == 'sources' else EchoCancellation.SINK}]
    def pulse(self, *args):
        self.calls.append(args)
        if args[0] == 'load-module':
            if self.fail_load: raise AudioError('audio_service_unavailable')
            return '77'
        return ''
    def audio_device(self, kind): return {'name': 'physical-' + kind}
    def close(self): self.calls.append(('close',))

class EchoCancellationTests(unittest.TestCase):
    def test_routes_reference_and_capture_as_a_pair(self):
        b = Backend(); echo = EchoCancellation(b)
        self.assertEqual(echo.prepare('speaker', 'mic'), (echo.SINK, echo.SOURCE))
        load = b.calls[0]
        self.assertIn('sink_master=speaker', load)
        self.assertIn('source_master=mic', load)
        self.assertIn('aec_method=webrtc', load)
        self.assertTrue(echo.active)
        echo.close(); echo.close()
        self.assertEqual(b.calls.count(('unload-module', '77')), 1)
    def test_microphone_off_does_not_open_filter(self):
        b = Backend(); echo = EchoCancellation(b)
        self.assertEqual(echo.prepare('speaker', None), ('speaker', None))
        self.assertEqual(b.calls, [])
    def test_unavailable_engine_preserves_raw_audio_and_reports_failure(self):
        b = Backend(); b.fail_load = True; echo = EchoCancellation(b)
        self.assertEqual(echo.prepare('speaker', 'mic'), ('speaker', 'mic'))
        self.assertFalse(echo.active)
        self.assertEqual(echo.error, 'echo_cancel_unavailable')
    def test_missing_pair_is_unloaded_before_fallback(self):
        b = Backend(); b.ready = False; echo = EchoCancellation(b)
        with patch('wifi_audio.time.sleep'):
            self.assertEqual(echo.prepare('speaker', 'mic'), ('speaker', 'mic'))
        self.assertIn(('unload-module', '77'), b.calls)
        self.assertFalse(echo.active)
    def test_only_our_stale_pair_is_removed(self):
        b = Backend()
        b.modules = [
            {'name': 'module-echo-cancel', 'index': 3, 'argument': 'source_name=other sink_name=other'},
            {'name': 'module-echo-cancel', 'index': 4,
             'argument': f'source_name={EchoCancellation.SOURCE} sink_name={EchoCancellation.SINK}'}]
        EchoCancellation(b).prepare('speaker', 'mic')
        self.assertIn(('unload-module', '4'), b.calls)
        self.assertNotIn(('unload-module', '3'), b.calls)
    def test_stream_start_failure_cleans_filter_and_releases_session(self):
        b = Backend(); audio = Mock(); audio.backend.return_value = b
        wifi = WiFiAudio(audio)
        with patch('wifi_audio.Session.start', side_effect=OSError('fake failure')):
            with self.assertRaises(OSError): wifi.serve(Mock(), True)
        self.assertIn(('unload-module', '77'), b.calls)
        self.assertEqual(b.calls[-1], ('close',))
        self.assertIsNone(wifi.session)
        audio.finish_wifi.assert_called_once()

if __name__ == '__main__': unittest.main()
