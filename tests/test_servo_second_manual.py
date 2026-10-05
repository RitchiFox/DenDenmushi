import unittest
from test_servo_manual import Driver
from servos import Servos, validate, FirstSweep, second_pulse
LEASE='6e552c68-1943-4e06-8203-caa37ae35573'
class SecondManualTests(unittest.TestCase):
    def setUp(self):
        self.now=0;self.d=Driver();self.s=Servos(self.d,clock=lambda:self.now,first_manual=True)
    def arm(self):self.s.command(dict(action='arm_second',angle=18))
    def step(self,d):self.s.command(dict(action='step_second',delta=d,expectedPulse=self.s.widths[1]))
    def smooth(self,start=3,end=33,duration=.5):
        return dict(action='smooth_second',startAngle=start,endAngle=end,durationSeconds=duration,expectedPulse=self.s.widths[1],lease=LEASE)
    def test_unknown_confirmation_and_rearm(self):
        with self.assertRaises(ValueError):self.s.command(dict(action='step_second',delta=1,expectedPulse=700))
        self.arm();self.assertEqual(self.d.calls,[])
        with self.assertRaises(ValueError):self.arm()
    def test_steps_limits_and_release(self):
        self.arm();previous=700
        for d,end in [(-1,533),(1,867)]:
            while self.s.widths[1]!=end:
                self.step(d);width=self.s.widths[1]
                self.assertLessEqual(abs(width-previous),11);previous=width
                self.assertTrue(533<=width<=867)
            with self.assertRaises(ValueError):self.step(d)
        self.now=.51;self.s.tick();self.assertEqual(self.d.calls[-1],(27,0))
        self.assertTrue(all(pin==27 for pin,width in self.d.calls))
    def test_all_actions_reject_outside_limits_without_output(self):
        for angle in [0,2.99,33.01,180,True,float('nan')]:
            with self.assertRaises(ValueError):self.s.command(dict(action='arm_second',angle=angle))
        self.arm()
        for angle in [0,2.99,33.01,180,True,float('nan')]:
            with self.assertRaises(ValueError):self.s.command(dict(action='move_second',angle=angle,expectedPulse=700))
            with self.assertRaises(ValueError):self.s.command(self.smooth(start=angle))
            with self.assertRaises(ValueError):self.s.command(self.smooth(end=angle))
        for pulse in [532,868,700.0,True]:
            with self.assertRaises(ValueError):validate(dict(action='step_second',delta=1,expectedPulse=pulse))
        with self.assertRaises(ValueError):self.s.command(dict(action='move',channel=2,angle=90))
        self.assertEqual(self.d.calls,[])
    def test_legacy_slider_bounded_and_stale(self):
        self.arm()
        for angle,width in [(3,533),(33,867),(18,700)]:
            self.s.command(dict(action='move_second',angle=angle,expectedPulse=self.s.widths[1]))
            self.assertEqual(self.d.calls[-1],(27,width))
        before=list(self.d.calls)
        with self.assertRaises(ValueError):self.s.command(dict(action='move_second',angle=20,expectedPulse=699))
        self.assertEqual(self.d.calls,before)
    def test_smooth_directions_and_endpoints(self):
        for start,end in [(3,33),(33,3),(10,20)]:
            sweep=FirstSweep(700,dict(startAngle=start,endAngle=end,durationSeconds=.3),second=True)
            self.assertTrue(all(533<=w<=867 and 3<=a<=33 for w,a,_ in sweep.steps))
            self.assertEqual(sweep.steps[-1][0],second_pulse(end))
        self.arm();self.s.command(self.smooth())
        for i in range(300):
            self.now+=.021
            if i%30==0:self.s.command(dict(action='keepalive',lease=LEASE))
            self.s.tick()
        self.assertEqual(self.s.widths[1],867);self.assertFalse(self.s.active[1]);self.assertIsNone(self.s.sweeps[1])
        self.assertTrue(all(c[0]==27 and (c[1]==0 or 533<=c[1]<=867) for c in self.d.calls if len(c)==2))
        self.assertEqual(self.s.widths[0],0);self.assertEqual(self.s.widths[2],0)
    def test_duplicate_stop_expiry_and_invalid_duration(self):
        self.arm()
        for duration in [.29,20.1,float('nan'),True]:
            with self.assertRaises(ValueError):self.s.command(self.smooth(duration=duration))
        body=self.smooth();self.s.command(body)
        with self.assertRaises(ValueError):self.s.command(body)
        self.s.command(dict(action='release',channel=2));before=list(self.d.calls)
        self.now=10;self.s.command(dict(action='keepalive',lease=LEASE));self.s.tick()
        self.assertEqual(self.d.calls,before)
        self.s.command(self.smooth());self.now+=4.1;self.s.tick()
        self.assertFalse(self.s.active[1]);self.assertIsNone(self.s.sweeps[1]);self.assertEqual(self.d.calls[-1],(27,0))
