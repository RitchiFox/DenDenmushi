import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from audio import Audio, AudioError, Backend
MAC='11:22:33:44:55:66'

class FakeBackend:
    def __init__(self): self.calls=[]; self.trusted=True; self.closed=False
    def device(self, mac):
        if not self.trusted: raise AudioError('pair_required')
        self.calls.append(('device', mac)); return '/device', '/adapter'
    def adapter_address(self, path): return 'AA:BB:CC:DD:EE:FF'
    def ensure_power(self, path): self.calls.append(('power',path))
    def connect_profile(self, path, mode='playback'): self.calls.append(('connect',path) if mode=='playback' else ('connect',path,mode))
    def disconnect_call_profile(self, path): self.calls.append(('disconnect-call',path))
    def remove_call_routes(self, mac): self.calls.append(('remove-call',mac))
    def route(self, mac): self.calls.append(('route',mac)); return 'usb'
    def close(self): self.closed=True

class AudioTests(unittest.TestCase):
    def test_untrusted_device_never_connects(self):
        b=FakeBackend(); b.trusted=False
        with self.assertRaisesRegex(AudioError,'pair_required'): Audio(lambda:b).connect(MAC)
        self.assertEqual(b.calls,[]); self.assertTrue(b.closed)
    def test_invalid_address_never_reaches_backend(self):
        for value in ['11:22:33:44:55:66; reboot',MAC+'\n',None,123,'abcd']:
            with self.assertRaisesRegex(AudioError,'invalid_address'): Audio(lambda:self.fail()).connect(value)
    def test_returns_own_adapter_identity_not_peer(self):
        b=FakeBackend(); result=Audio(lambda:b).connect(MAC)
        self.assertEqual(result['adapterAddress'],'AA:BB:CC:DD:EE:FF')
        self.assertEqual(b.calls,[('device',MAC),('power','/adapter'),('remove-call',MAC),('disconnect-call','/device'),('connect','/device'),('route',MAC)])
        self.assertTrue(b.closed)
    def test_parallel_request_does_not_duplicate_routing(self):
        a=Audio(lambda:self.fail()); a.lock.acquire()
        with self.assertRaisesRegex(AudioError,'audio_busy'): a.connect(MAC)
        a.lock.release()
    def test_failure_releases_lock_for_retry(self):
        b=FakeBackend(); b.trusted=False; a=Audio(lambda:b)
        with self.assertRaises(AudioError): a.connect(MAC)
        b.trusted=True
        self.assertTrue(a.connect(MAC)['ok'])

class AdapterPowerTests(unittest.TestCase):
    def backend(self, powered, fail=False):
        class DBusError(Exception): pass
        props = SimpleNamespace(powered=powered, calls=[])
        def get(*args, **kwargs): return props.powered
        def set_value(interface, name, value, **kwargs):
            props.calls.append((interface,name,value))
            if fail: raise DBusError('Authentication Failed')
            props.powered=value
        props.Get=get; props.Set=set_value
        backend=object.__new__(Backend)
        backend.dbus=SimpleNamespace(Interface=lambda *args:props, Boolean=bool, DBusException=DBusError)
        backend.bus=SimpleNamespace(get_object=lambda *args:None)
        return backend, props
    def test_powered_adapter_is_not_reset(self):
        backend, props=self.backend(True)
        backend.ensure_power('/adapter')
        self.assertEqual(props.calls,[])
    def test_explicit_connection_can_restore_disabled_adapter(self):
        backend, props=self.backend(False)
        backend.ensure_power('/adapter')
        self.assertEqual(props.calls,[('org.bluez.Adapter1','Powered',True)])
    def test_failed_power_restore_is_specific_and_stops_before_profile(self):
        backend, _=self.backend(False,True)
        with self.assertRaisesRegex(AudioError,'bluetooth_power_unavailable') as error:
            backend.ensure_power('/adapter')
        self.assertIn('Authentication Failed',error.exception.detail)
        fake=FakeBackend()
        fake.ensure_power=lambda adapter:backend.ensure_power(adapter)
        with self.assertRaisesRegex(AudioError,'bluetooth_power_unavailable'):
            Audio(lambda:fake).connect(MAC)
        self.assertFalse(any(call[0]=='connect' for call in fake.calls))
        self.assertTrue(fake.closed)

class ProfileConnectionTests(unittest.TestCase):
    class DBusError(Exception):
        def __init__(self, name, detail='br-connection-busy'):
            super().__init__(detail)
            self.name = name
        def get_dbus_name(self):
            return self.name

    def setUp(self):
        self.now = 0.0; self.waits = []; self.calls = []
        def sleep(duration):
            self.waits.append(duration)
            self.now += duration
        clock = patch('audio.time.monotonic', side_effect=lambda: self.now)
        sleeper = patch('audio.time.sleep', side_effect=sleep)
        clock.start(); sleeper.start()
        self.addCleanup(clock.stop); self.addCleanup(sleeper.stop)

    def backend(self, errors, duration=0):
        attempts = [0]
        backend = object.__new__(Backend)
        def interface(path, name):
            self.assertEqual(name, 'org.bluez.Device1')
            def connect(profile, timeout):
                self.calls.append((path, profile, timeout))
                self.now += min(duration, timeout)
                error = errors[min(attempts[0], len(errors)-1)]
                attempts[0] += 1
                if error is not None:
                    raise error
            # No disconnect/reset method is offered by this fake interface.
            return SimpleNamespace(ConnectProfile=connect)
        backend.dbus = SimpleNamespace(Interface=interface, DBusException=self.DBusError)
        def device(service, path):
            self.assertEqual(service, 'org.bluez')
            return path
        backend.bus = SimpleNamespace(get_object=device)
        return backend

    def test_in_progress_retries_same_peer_and_profile_without_teardown(self):
        busy = self.DBusError('org.bluez.Error.InProgress')
        for mode, profile in [('call', '0000111f-0000-1000-8000-00805f9b34fb'),
                              ('playback', '0000110a-0000-1000-8000-00805f9b34fb')]:
            with self.subTest(mode=mode):
                self.now = 0; self.calls.clear(); self.waits.clear()
                self.backend([busy, busy, None]).connect_profile('/exact-peer', mode)
                self.assertEqual([(path, value) for path, value, _ in self.calls],
                                 [('/exact-peer', profile)] * 3)
                self.assertEqual(self.waits, [0.25, 0.25])
                self.assertEqual([timeout for _, _, timeout in self.calls], [12, 11.75, 11.5])

    def test_already_connected_succeeds_without_retry(self):
        self.backend([self.DBusError('org.bluez.Error.AlreadyConnected')]).connect_profile('/exact-peer', 'call')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.waits, [])

    def test_other_errors_fail_immediately_even_when_text_says_busy(self):
        for name in ('org.bluez.Error.Failed', 'org.bluez.Error.NotReady', 'org.freedesktop.DBus.Error.NoReply'):
            with self.subTest(name=name):
                self.calls.clear()
                with self.assertRaisesRegex(AudioError, 'bluetooth_unavailable') as failure:
                    self.backend([self.DBusError(name)]).connect_profile('/exact-peer', 'call')
                self.assertEqual(failure.exception.detail, 'br-connection-busy')
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.waits, [])

    def test_busy_retries_keep_original_deadline_and_final_error(self):
        busy = self.DBusError('org.bluez.Error.InProgress', 'still connecting')
        for duration in (0, 3):
            with self.subTest(duration=duration):
                self.now = 0; self.calls.clear(); self.waits.clear()
                with self.assertRaisesRegex(AudioError, 'bluetooth_unavailable') as failure:
                    self.backend([busy], duration=duration).connect_profile('/exact-peer', 'call')
                self.assertEqual(self.now, 12)
                self.assertEqual(failure.exception.detail, 'still connecting')
                self.assertLessEqual(len(self.calls), 48)
                timeouts = [timeout for _, _, timeout in self.calls]
                self.assertTrue(all(0 < value <= 12 for value in timeouts))
                self.assertTrue(all(later < earlier for earlier, later in zip(timeouts, timeouts[1:])))


class RoutingTests(unittest.TestCase):
    def backend(self, source=True, stream=False, existing=False):
        b=object.__new__(Backend); b.calls=[]
        src='bluez_input.11_22_33_44_55_66.0'; sink='alsa_output.usb-Realtek.analog-stereo'
        values={'sinks':[{'name':sink}], 'sources':[{'name':src}] if source else [],
                'sink-inputs':[{'index':12,'properties':{'node.name':src}}] if stream else [],
                'modules':[{'name':'module-loopback','argument':f'source={src} sink={sink}'}] if existing else []}
        b.items=lambda kind:values[kind]
        b.pulse=lambda *args:b.calls.append(args)
        return b,values
    def test_existing_exact_loopback_is_reused(self):
        b,_=self.backend(existing=True); b.route(MAC); b.route(MAC)
        self.assertFalse(any(c[0]=='load-module' for c in b.calls))
    def test_playback_stream_moves_without_extra_loopback(self):
        b,_=self.backend(stream=True); b.route(MAC)
        self.assertTrue(any(c[0]=='move-sink-input' for c in b.calls))
        self.assertFalse(any(c[0]=='load-module' for c in b.calls))
    def test_input_mode_creates_single_targeted_loopback(self):
        b,_=self.backend(); b.route(MAC)
        self.assertEqual(sum(c[0]=='load-module' for c in b.calls),1)
        self.assertIn('sink_dont_move=true',b.calls[-1])
    def test_multiple_usb_cards_require_selection(self):
        b,v=self.backend(); v['sinks'].append({'name':'alsa_output.usb-Other'})
        with self.assertRaisesRegex(AudioError,'usb_output_ambiguous'): b.route(MAC)
        self.assertFalse(b.calls)

    def test_missing_usb_never_falls_back_to_hdmi(self):
        b,v=self.backend(); v['sinks']=[{'name':'alsa_output.hdmi'}]
        with self.assertRaisesRegex(AudioError,'usb_output_missing'): b.route(MAC)
        self.assertFalse(b.calls)

class LevelTests(unittest.TestCase):
    def test_input_only_positive_gain_is_bounded(self):
        a=Audio(); calls=[]
        a.volume_operation=lambda *args:calls.append(args)
        a.set_level('input',12,False); a.set_level('input',20,False)
        self.assertEqual(calls,[('input',12,False),('input',20,False)])
        for kind,db in [('input',21),('output',1)]:
            with self.assertRaisesRegex(AudioError,'invalid_level'): a.set_level(kind,db,False)
        b=object.__new__(Backend)
        b.items=lambda kind:[{'name':'alsa_input.usb-A' if kind=='sources' else 'alsa_output.usb-A',
                              'volume':{'mono':{'value':65536*10**(12/60)}},'mute':False}]
        self.assertAlmostEqual(b.levels()['input']['db'],12)
        self.assertEqual(b.levels()['output']['db'],0)

    def test_invalid_level_cannot_execute_commands(self):
        for kind, db, muted in [('output', 1, False), ('input', -61, False), ('other', -10, False),
                                ('input', float('nan'), False), ('output', float('inf'), False),
                                ('output', True, False), ('output', '0; reboot', False), ('output', 0, 1)]:
            with self.assertRaisesRegex(AudioError, 'invalid_level'):
                Audio(lambda:self.fail()).set_level(kind, db, muted)

    def test_levels_read_hardware_without_changing_it_and_exclude_monitor(self):
        b=object.__new__(Backend)
        b.items=lambda kind: ([{'name':'alsa_output.usb-A','volume':{'left':{'value':65536}},'mute':False}]
            if kind=='sinks' else [{'name':'alsa_output.usb-A.monitor','properties':{'device.bus':'usb'}}])
        result=b.levels()
        self.assertEqual(result['output'], {'available':True,'db':0,'muted':False,'connection':'usb'})
        self.assertFalse(result['input']['available'])

    def test_half_pulse_volume_is_minus_18_db_and_mute_is_separate(self):
        b=object.__new__(Backend)
        b.items=lambda kind: [{'name':'alsa_output.usb-A' if kind=='sinks' else 'alsa_input.usb-A',
                               'volume':{'left':{'value':32768}},'mute':True}]
        result=b.levels()
        self.assertAlmostEqual(result['output']['db'], -18.0618, places=3)
        self.assertTrue(result['input']['muted'])

    def test_set_only_requested_usb_channel_and_read_back(self):
        b=object.__new__(Backend); calls=[]
        b.items=lambda kind: [{'name':'alsa_input.usb-Mic'}]
        b.pulse=lambda *args:calls.append(args)
        b.levels=lambda:{'ok':True}
        self.assertEqual(b.set_level('input', -10, True), {'ok':True})
        self.assertEqual(calls, [('set-source-volume','alsa_input.usb-Mic','-10.00dB'),
                                ('set-source-mute','alsa_input.usb-Mic','1')])

class SplitAudioTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', DENDEN_SPEAKER_OUTPUT='headphones')
        self.env.start(); self.addCleanup(self.env.stop)
        self.backend, self.values = RoutingTests().backend()
        self.headphones = {'name':'alsa_output.platform-mailbox.2', 'index':8,
            'properties':{'alsa.card_name':'bcm2835 Headphones'},
            'volume':{'mono':{'value':65536}}, 'mute':False}
        self.values['sinks'] += [{'name':'alsa_output.hdmi',
            'properties':{'alsa.card_name':'bcm2835 HDMI 1'}}, self.headphones]

    def test_route_uses_headphones_with_usb_and_hdmi_present(self):
        self.assertEqual(self.backend.route(MAC), self.headphones['name'])
        self.assertIn(('set-default-sink', self.headphones['name']), self.backend.calls)
        self.assertFalse(any('sink=alsa_output.usb-' in ' '.join(c) for c in self.backend.calls))

    def test_missing_analog_output_never_falls_back_to_usb_or_hdmi(self):
        self.values['sinks'].remove(self.headphones)
        with self.assertRaisesRegex(AudioError, 'headphone_output_missing'):
            self.backend.route(MAC)
        self.assertEqual(self.backend.calls, [])

    def test_multiple_analog_outputs_are_not_guessed(self):
        self.values['sinks'].append(dict(self.headphones, name='duplicate'))
        with self.assertRaisesRegex(AudioError, 'headphone_output_ambiguous'):
            self.backend.route(MAC)

    def test_levels_and_mutes_target_analog_output_but_usb_capture(self):
        mic = {'name':'alsa_input.usb-Realtek.analog-stereo',
               'volume':{'left':{'value':65536}}, 'mute':False}
        self.values['sources'].append(mic)
        result = self.backend.set_level('output', -18, False)
        self.assertEqual(result['output']['connection'], 'headphones')
        self.assertEqual(result['input']['connection'], 'usb')
        self.assertIn(('set-sink-volume',self.headphones['name'],'-18.00dB'), self.backend.calls)
        self.backend.set_level('input', -6, True)
        self.assertIn(('set-source-volume',mic['name'],'-6.00dB'), self.backend.calls)
        self.assertIn(('set-source-mute',mic['name'],'1'), self.backend.calls)
        self.backend.route(MAC)
        self.assertIn(('set-default-source',mic['name']), self.backend.calls)

    def test_unplugged_usb_mic_does_not_remove_analog_output(self):
        result = self.backend.levels()
        self.assertTrue(result['output']['available'])
        self.assertFalse(result['input']['available'])

    def test_old_application_loopback_removed_but_other_routes_preserved(self):
        source = self.values['sources'][0]['name']
        args = f'source={source} sink=alsa_output.usb-Realtek latency_msec=60 source_dont_move=true sink_dont_move=true'
        self.values['modules'] = [
            {'index':42, 'name':'module-loopback', 'argument':args},
            {'index':43, 'name':'module-loopback', 'argument':f'source={source} sink=other'}]
        self.backend.route(MAC)
        self.assertIn(('unload-module','42'), self.backend.calls)
        self.assertNotIn(('unload-module','43'), self.backend.calls)

    def test_quiet_mute_controls_analog_only(self):
        from quiet import QuietOutput
        now = [0]
        quiet = QuietOutput(clock=lambda:now[0])
        self.backend.playback_active = lambda item: False
        quiet.tick(self.backend)
        now[0] = 3
        quiet.tick(self.backend)
        self.assertIn(('set-sink-mute',self.headphones['name'],'1'),self.backend.calls)
        self.assertFalse(any('usb-' in ' '.join(c) for c in self.backend.calls))

class CallRoutingTests(unittest.TestCase):
    def backend(self, native=True):
        b=object.__new__(Backend); b.calls=[]
        props={'api.bluez5.address':MAC,'api.bluez5.profile':'headset-audio-gateway','api.bluez5.codec':'msbc'}
        usb_in={'index':11,'name':'alsa_input.usb-Realtek','mute':False,'state':'RUNNING','volume':{'mono':{'value':65536}}}
        usb_out={'index':12,'name':'alsa_output.usb-Realtek','mute':False}
        self.values={'cards':[{'index':20,'name':'bluez_card.11_22_33_44_55_66','properties':props,
                     'profiles':{'audio-gateway':{'available':'yes'}},'active_profile':'audio-gateway'}],
                     'sources':[usb_in], 'sinks':[usb_out], 'modules':[], 'source-outputs':[], 'sink-inputs':[]}
        if native:
            self.values['source-outputs']=[{'index':31,'source':99,'corked':False,'mute':False,'properties':dict(props,**{'node.name':'bluez_output.11_22_33_44_55_66.0'})}]
            self.values['sink-inputs']=[{'index':32,'sink':88,'corked':False,'properties':dict(props,**{'node.name':'bluez_input.11_22_33_44_55_66.0'})}]
        b.items=lambda kind:self.values[kind]
        b.device=lambda mac:('/peer','/adapter')
        b.objects=lambda:{'/peer':{'org.bluez.Device1':{'Connected':True}}}
        def pulse(*args):
            b.calls.append(args)
            if args[0] in ('move-source-output','move-sink-input'):
                output=args[0]=='move-sink-input'; kind='sink-inputs' if output else 'source-outputs'; key='sink' if output else 'source'
                endpoint=next(x for x in self.values['sinks' if output else 'sources'] if x['name']==args[2])
                next(x for x in self.values[kind] if str(x['index'])==args[1])[key]=endpoint['index']
            elif args[0]=='set-card-profile': self.values['cards'][0]['active_profile']=args[2]
            elif args[0]=='unload-module':
                self.values['modules'][:]=[x for x in self.values['modules'] if str(x['index'])!=args[1]]
                for kind in ('source-outputs','sink-inputs'):
                    self.values[kind][:]=[x for x in self.values[kind] if str(x.get('owner_module'))!=args[1]]
            elif args[0]=='load-module':
                index=100+sum(c[0]=='load-module' for c in b.calls)
                module={'index':index,'name':args[1],'argument':' '.join(args[2:])}; self.values['modules'].append(module)
                parsed=Backend.module_args(module)
                for kind, endpoints, key in [('source-outputs','sources','source'),('sink-inputs','sinks','sink')]:
                    endpoint=next(x for x in self.values[endpoints] if x['name']==parsed[key])
                    self.values[kind].append({'index':index*10,'owner_module':index,key:endpoint['index'],'corked':False})
                return str(index)
            return ''
        b.pulse=pulse
        return b,props

    def test_native_ag_moves_capture_and_playback_without_local_mic_loop(self):
        b,_=self.backend()
        other={'index':39,'source':98,'corked':False,'properties':{'api.bluez5.address':'00:00:00:00:00:00','api.bluez5.profile':'headset-audio-gateway'}}
        self.values['source-outputs'].append(other)
        result=b.reconcile_call(MAC)
        self.assertTrue(result['microphoneReady']); self.assertTrue(result['speakerReady'])
        self.assertIn(('move-source-output','31','alsa_input.usb-Realtek'),b.calls)
        self.assertIn(('move-sink-input','32','alsa_output.usb-Realtek'),b.calls)
        self.assertEqual(other['source'],98)
        self.assertFalse(any(c[0]=='load-module' for c in b.calls))
        b.calls.clear(); b.reconcile_call(MAC)
        self.assertFalse(any(c[0].startswith('move-') for c in b.calls))

    def test_pending_then_recreated_native_streams_are_reacquired(self):
        b,props=self.backend(False)
        self.assertFalse(b.reconcile_call(MAC)['microphoneReady'])
        self.values['source-outputs']=[{'index':55,'source':97,'corked':False,'properties':props}]
        self.values['sink-inputs']=[{'index':56,'sink':96,'corked':False,'properties':props}]
        self.assertTrue(b.reconcile_call(MAC)['microphoneReady'])
        self.values['source-outputs'][0].update(index=77,source=97)
        b.reconcile_call(MAC)
        self.assertIn(('move-source-output','77','alsa_input.usb-Realtek'),b.calls)

    def test_new_native_capture_moves_before_module_scans_and_default_writes(self):
        b,_=self.backend()
        events=[]; items=b.items; pulse=b.pulse
        def read(kind):
            events.append(('read',kind))
            return items(kind)
        def execute(*args):
            events.append(args)
            return pulse(*args)
        b.items=read; b.pulse=execute
        self.values['modules'].append({'index':900,'name':'module-loopback',
            'argument':'source=bluez_input.11_22_33_44_55_66.0 sink=old latency_msec=60 source_dont_move=true sink_dont_move=true'})
        self.assertTrue(b.reconcile_call(MAC)['microphoneReady'])
        moved=events.index(('move-source-output','31','alsa_input.usb-Realtek'))
        self.assertLess(moved,events.index(('read','modules')))
        self.assertLess(moved,events.index(('unload-module','900')))
        self.assertLess(moved,events.index(('set-default-source','alsa_input.usb-Realtek')))

    def test_established_native_streams_are_checked_without_mutations_or_second_snapshot(self):
        b,_=self.backend(); b.reconcile_call(MAC); b.calls.clear()
        # An unrelated module must neither force maintenance nor be removed.
        self.values['modules'].append({'index':999,'name':'module-loopback','argument':'source=other sink=other'})
        reads=[]; items=b.items
        def read(kind):
            reads.append(kind)
            return items(kind)
        b.items=read
        b.pulse=lambda *args:self.fail('Established native route was modified: '+repr(args))
        result=b.reconcile_call(MAC)
        self.assertTrue(result['microphoneReady']); self.assertTrue(result['speakerReady'])
        self.assertEqual(reads,['cards','sources','sinks','sink-inputs','source-outputs','modules'])
        # Status must reflect a later user mute, not a value cached last tick.
        self.values['source-outputs'][0]['mute']=True
        self.assertTrue(b.reconcile_call(MAC)['microphoneMuted'])

    def test_established_native_path_still_removes_exact_owned_and_legacy_modules(self):
        for owned in (True, False):
            with self.subTest(owned=owned):
                b,_=self.backend(); b.reconcile_call(MAC); b.calls.clear()
                if owned:
                    marker='denden.route='+Backend.call_tag(MAC,'microphone')
                    args='source=old sink=old source_output_properties='+marker+' sink_input_properties='+marker
                else:
                    args='source=bluez_input.11_22_33_44_55_66.0 sink=old latency_msec=60 source_dont_move=true sink_dont_move=true'
                self.values['modules'].append({'index':900,'name':'module-loopback','argument':args})
                self.assertTrue(b.reconcile_call(MAC)['microphoneReady'])
                self.assertIn(('unload-module','900'),b.calls)
                self.assertEqual(self.values['modules'],[])

    def test_established_native_path_rechecks_recreated_nodes_and_unplugged_usb(self):
        b,_=self.backend(); b.reconcile_call(MAC); b.calls.clear()
        self.values['source-outputs'][0].update(index=77,source=97)
        self.assertTrue(b.reconcile_call(MAC)['microphoneReady'])
        self.assertIn(('move-source-output','77','alsa_input.usb-Realtek'),b.calls)
        b.calls.clear(); self.values['sources'].clear()
        with self.assertRaisesRegex(AudioError,'usb_input_missing'):
            b.reconcile_call(MAC)
        self.assertFalse(b.calls)

    def test_corked_native_stream_does_not_take_established_fast_path(self):
        b,_=self.backend(); b.reconcile_call(MAC); b.calls.clear()
        self.values['source-outputs'][0]['corked']=True
        result=b.reconcile_call(MAC)
        self.assertFalse(result['microphoneReady'])
        # Pending streams retain full reconciliation and a fresh final status.
        self.assertIn(('set-default-source','alsa_input.usb-Realtek'),b.calls)

    def test_snapshot_reuses_physical_endpoint_lists_but_still_rejects_ambiguity(self):
        b,_=self.backend(); reads=[]; items=b.items
        def read(kind):
            reads.append(kind)
            return items(kind)
        b.items=read
        snap=b.call_snapshot(MAC)
        self.assertEqual(snap['usbInput']['index'],11)
        self.assertEqual(snap['usbOutput']['index'],12)
        self.assertEqual(reads.count('sources'),1)
        self.assertEqual(reads.count('sinks'),1)
        self.values['sources'].append({'index':13,'name':'alsa_input.usb-Other'})
        with self.assertRaisesRegex(AudioError,'usb_input_ambiguous'):
            b.reconcile_call(MAC)
        self.assertFalse(b.calls)
        # An explicitly empty snapshot cannot fall back to a later device list.
        with self.assertRaisesRegex(AudioError,'usb_input_missing'):
            b.audio_device('input',[])

    def test_muted_or_corked_mic_is_not_ready_and_diagnostics_preserve_levels(self):
        b,_=self.backend(); b.reconcile_call(MAC); b.calls.clear()
        self.values['source-outputs'][0]['mute']=True
        result=b.call_status(MAC)
        self.assertTrue(result['microphoneRouteReady']); self.assertFalse(result['microphoneReady'])
        self.assertTrue(result['microphoneStreams'][0]['muted'])
        self.assertEqual(result['usbCapture']['volume']['mono']['value'],65536)
        self.assertEqual(b.calls,[])
        self.values['source-outputs'][0].update(mute=False,corked=True)
        self.assertFalse(b.call_status(MAC)['microphoneReady'])

    def test_fallback_loopback_is_tagged_reused_and_recreated_only_for_peer(self):
        b,props=self.backend(False)
        self.values['sinks'].append({'index':41,'name':'bluez_output.11_22_33_44_55_66.0','properties':props})
        self.values['sources'].append({'index':42,'name':'bluez_input.11_22_33_44_55_66.0','properties':props})
        self.values['modules'].append({'index':999,'name':'module-loopback','argument':'source=other sink=other'})
        self.assertTrue(b.reconcile_call(MAC)['microphoneReady'])
        self.assertEqual(sum(c[0]=='load-module' for c in b.calls),2)
        b.reconcile_call(MAC)
        self.assertEqual(sum(c[0]=='load-module' for c in b.calls),2)
        self.values['sinks'][1].update(name='bluez_output.11_22_33_44_55_66.1',index=43)
        self.assertTrue(b.reconcile_call(MAC)['microphoneReady'])
        self.assertEqual(sum(c[0]=='load-module' for c in b.calls),3)
        b.remove_call_routes(MAC)
        self.assertEqual([x['index'] for x in self.values['modules']],[999])

    def test_profile_and_usb_identity_preflight(self):
        b,_=self.backend()
        self.values['cards'][0]['active_profile']='off'; b.prepare_call(MAC)
        self.assertEqual(b.calls[-1][2],'audio-gateway')
        self.values['sources'] += [{'index':13,'name':'alsa_input.usb-Other'}]
        with self.assertRaisesRegex(AudioError,'usb_input_ambiguous'): b.prepare_call(MAC)
        self.assertFalse(Backend.peer_item({'name':'bluez_input.'+MAC+'.0','properties':{'api.bluez5.address':'00:00:00:00:00:00'}},MAC))


class CallPreparationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.waits = []
        def advance(duration):
            self.waits.append(duration)
            self.now += duration
        clock = patch('audio.time.monotonic', side_effect=lambda: self.now)
        sleep = patch('audio.time.sleep', side_effect=advance)
        clock.start(); sleep.start()
        self.addCleanup(clock.stop); self.addCleanup(sleep.stop)
        self.card = {'name': 'bluez_card.' + MAC.replace(':', '_'),
                     'properties': {'api.bluez5.address': MAC},
                     'profiles': {'audio-gateway': {'available': 'yes'}}, 'active_profile': 'off'}
        self.backend = object.__new__(Backend)
        self.endpoints = []; self.commands = []; self.reads = 0
        def endpoint(kind):
            self.endpoints.append(kind)
            return {'name': 'usb-' + kind}
        self.backend.audio_device = endpoint
        self.backend.pulse = lambda *args: self.commands.append(args)

    def snapshots(self, cards):
        def items(kind):
            self.assertEqual(kind, 'cards')
            result = cards[min(self.reads, len(cards) - 1)]
            self.reads += 1
            return result
        self.backend.items = items

    def test_waits_for_exact_peer_card_then_available_profile_without_reconnecting(self):
        other = dict(self.card, properties={'api.bluez5.address': '00:00:00:00:00:00'})
        missing_profile = dict(self.card, profiles={})
        pending_profile = dict(self.card, profiles=[{'name': 'audio-gateway', 'available': 'no'}])
        self.snapshots([[other], [other, missing_profile], [pending_profile], [self.card]])
        self.backend.prepare_call(MAC)
        self.assertEqual(self.endpoints, ['input', 'output'])
        self.assertEqual(self.reads, 4)
        self.assertGreater(self.now, 0)
        self.assertLess(self.now, 4)
        self.assertEqual(self.commands, [('set-default-source', 'usb-input'),
                                         ('set-default-sink', 'usb-output'),
                                         ('set-card-profile', self.card['name'], 'audio-gateway')])

    def test_defaults_precede_profile_activation_and_are_seeded_for_existing_profile(self):
        for active in ('off', 'audio-gateway'):
            with self.subTest(active=active):
                self.commands.clear(); self.reads = 0
                self.snapshots([[dict(self.card, active_profile=active)]])
                self.backend.prepare_call(MAC)
                self.assertEqual(self.commands[:2], [('set-default-source', 'usb-input'),
                                                     ('set-default-sink', 'usb-output')])
                if active == 'off':
                    self.assertEqual(self.commands[2], ('set-card-profile', self.card['name'], 'audio-gateway'))
                else:
                    self.assertEqual(len(self.commands), 2)

    def test_missing_card_and_unavailable_profile_have_bounded_wait_and_specific_error(self):
        for cards, code in [([], 'bluetooth_card_missing'),
                            ([dict(self.card, profiles={})], 'hfp_profile_unavailable')]:
            with self.subTest(code=code):
                self.now = 0; self.reads = 0; self.waits.clear()
                self.snapshots([cards])
                with self.assertRaisesRegex(AudioError, code):
                    self.backend.prepare_call(MAC)
                self.assertEqual(self.now, 4)
                self.assertGreater(self.reads, 1)
                self.assertFalse(self.commands)

    def test_ambiguous_card_is_not_retried_or_selected(self):
        self.snapshots([[self.card, dict(self.card, name='duplicate')]])
        with self.assertRaisesRegex(AudioError, 'bluetooth_card_ambiguous'):
            self.backend.prepare_call(MAC)
        self.assertEqual(self.reads, 1)
        self.assertFalse(self.waits)
        self.assertFalse(self.commands)

    def test_missing_usb_stops_before_card_discovery(self):
        self.snapshots([[self.card]])
        def missing(kind): raise AudioError('usb_input_missing')
        self.backend.audio_device = missing
        with self.assertRaisesRegex(AudioError, 'usb_input_missing'):
            self.backend.prepare_call(MAC)
        self.assertEqual(self.reads, 0)
        self.assertFalse(self.waits)
        self.assertFalse(self.commands)

    def test_audio_service_failure_is_not_hidden_by_readiness_retry(self):
        self.snapshots([[self.card]])
        def failed(*args):
            if args[0] == 'set-card-profile':
                raise AudioError('audio_service_unavailable', 'profile switch failed')
        self.backend.pulse = failed
        with self.assertRaisesRegex(AudioError, 'audio_service_unavailable') as result:
            self.backend.prepare_call(MAC)
        self.assertEqual(result.exception.detail, 'profile switch failed')
        self.assertEqual(self.reads, 1)
        self.assertFalse(self.waits)


class CallLeaseTests(unittest.TestCase):
    def setup_audio(self):
        self.now=[0]; b=FakeBackend()
        b.audio_device=lambda kind:{'name':'usb-'+kind}
        b.prepare_call=lambda mac:None
        b.reconcile_call=lambda mac:{'microphoneReady':True,'speakerReady':True}
        b.call_status=b.reconcile_call
        return Audio(lambda:b,clock=lambda:self.now[0]),b

    def test_explicit_microphone_lease_and_read_only_status(self):
        a,b=self.setup_audio(); self.assertTrue(a.connect(MAC,'call')['ok'])
        self.now[0]=10; a.status(); self.assertEqual(a.call_until,30)
        a.lock.acquire()
        try: a.heartbeat(True)
        finally: a.lock.release()
        self.assertEqual(a.call_until,40)
        self.now[0]=41; a.heartbeat(True); a.tick()
        self.assertEqual(a.mode,'playback'); self.assertIn(('disconnect-call','/device'),b.calls)
        self.assertFalse(a.status()['callRequested'])

    def test_camera_only_heartbeat_stops_mic_without_stopping_camera(self):
        a,b=self.setup_audio(); a.connect(MAC,'call'); a.heartbeat(False); a.tick()
        self.assertEqual(a.mode,'playback'); self.assertEqual(a.call_until,0)

    def test_prepare_failure_closes_profile_before_any_lease(self):
        a,b=self.setup_audio()
        def fail(mac): raise AudioError('hfp_profile_unavailable')
        b.prepare_call=fail
        result=a.connect(MAC,'call')
        self.assertFalse(result['callRequested']); self.assertEqual(result['error'],'hfp_profile_unavailable')
        self.assertIn(('disconnect-call','/device'),b.calls); self.assertEqual(a.call_until,0)

    def test_failed_unload_still_closes_native_capture_and_retries_cleanup(self):
        a,b=self.setup_audio(); a.connect(MAC,'call')
        def fail(mac): raise AudioError('audio_service_unavailable')
        b.remove_call_routes=fail; b.calls.clear(); self.now[0]=31; a.tick()
        self.assertIn(('disconnect-call','/device'),b.calls)
        self.assertEqual(a.mode,'call'); self.assertFalse(a.connection_state()['callRequested'])
        b.remove_call_routes=lambda mac:None; a.tick(); self.assertEqual(a.mode,'playback')

    def test_playback_returns_to_existing_route_and_pending_is_not_success(self):
        a,b=self.setup_audio(); b.reconcile_call=lambda mac:{'microphoneReady':False,'speakerReady':False}
        with patch('audio.time.sleep'):
            pending=a.connect(MAC,'call')
        self.assertFalse(pending['ok']); self.assertTrue(pending['callRequested']); self.assertEqual(pending['error'],'microphone_pending')
        result=a.connect(MAC)
        self.assertTrue(result['ok']); self.assertEqual(result['mode'],'playback'); self.assertFalse(result['microphoneReady'])
        self.assertIn(('route',MAC),b.calls)

if __name__=='__main__':unittest.main()
