import unittest
from test_servo_manual import Driver
from servos import Servos, validate, FirstSweep

LEASE='6e552c68-1943-4e06-8203-caa37ae35573'
class FirstSmoothTests(unittest.TestCase):
    def setUp(self):
        self.now=10.0;self.d=Driver();self.s=Servos(self.d,clock=lambda:self.now,first_manual=True)
    def body(self,start=12.3,end=45,duration=.5):
        return dict(action='smooth_first',expectedPulse=self.s.widths[0],startAngle=start,endAngle=end,durationSeconds=duration,lease=LEASE)
    def arm(self,angle=45):self.s.command(dict(action='arm_first',angle=angle))
    def test_unknown_and_invalid_do_not_emit(self):
        body=self.body();body['expectedPulse']=1000
        with self.assertRaises(ValueError):self.s.command(body)
        self.arm()
        for key,val in [('startAngle',12.2),('endAngle',45.6),('endAngle',float('nan')),('durationSeconds',0.29),('durationSeconds',21),('startAngle',True),('expectedPulse',999),('lease','bad')]:
            b=self.body();b[key]=val
            with self.subTest(key=key,val=val),self.assertRaises(ValueError):self.s.command(b)
        self.assertEqual(self.d.calls,[])
    def test_both_directions_and_preparation_stay_in_bounds(self):
        for start,end in [(12.3,45.5),(45.5,12.3),(23,18)]:
            sweep=FirstSweep(800,dict(startAngle=start,endAngle=end,durationSeconds=0.3))
            previous=800
            for width,angle,preparing in sweep.steps:
                self.assertTrue(637<=width<=1005)
                self.assertLessEqual(abs(width-previous),37)
                previous=width
            self.assertEqual(previous,max(637,min(1005,round(500+end*1000/90))))
    def test_lease_completion_and_other_channels(self):
        self.arm();self.s.command(self.body(45,12.3))
        for i in range(320):
            self.now+=.021
            if i%35==0:self.s.command(dict(action='keepalive',lease=LEASE))
            self.s.tick()
        self.assertEqual(self.s.widths[0],637)
        self.assertFalse(self.s.active[0]);self.assertIsNone(self.s.sweeps[0])
        self.assertTrue(all(pin==17 and (width==0 or 637<=width<=1005) for pin,width in (c for c in self.d.calls if len(c)==2)))
        self.assertFalse(any(self.s.active[1:]))
    def test_duplicate_stop_and_late_keepalive_never_resume(self):
        self.arm();body=self.body();self.s.command(body)
        before=list(self.d.calls)
        with self.assertRaises(ValueError):self.s.command(body)
        self.assertEqual(self.d.calls,before)
        self.s.command(dict(action='release',channel=1))
        before=list(self.d.calls)
        for _ in range(20):
            self.now+=1;self.s.command(dict(action='keepalive',lease=LEASE));self.s.tick()
        self.assertEqual(self.d.calls,before)
    def test_expiry_checked_before_trajectory_and_no_catchup(self):
        self.arm();self.s.command(self.body())
        self.now+=1;self.s.tick()
        self.assertLessEqual(len(self.d.calls),2)
        self.now+=4;self.s.tick()
        self.assertEqual(self.d.calls[-1],(17,0));self.assertIsNone(self.s.sweeps[0])
    def test_custom_reference_metadata_only(self):
        self.arm(12.3)
        self.assertEqual(self.s.widths[0],637);self.assertEqual(self.d.calls,[])
        for angle in [12.2,45.6,float('inf'),True]:
            with self.assertRaises(ValueError):validate(dict(action='arm_first',angle=angle))

if __name__=='__main__':unittest.main()
