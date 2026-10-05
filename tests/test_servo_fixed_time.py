import unittest
from test_servo_manual import Driver
from servos import Servos, FirstSweep, validate
LEASE='6e552c68-1943-4e06-8203-caa37ae35573'

class FixedTimeTests(unittest.TestCase):
    def test_all_smooth_commands_reject_other_durations(self):
        for action,a,b,pulse in [('smooth_first',12.3,45.5,637),('smooth_second',3,33,533),('smooth_third',30,60,833)]:
            body=dict(action=action,startAngle=a,endAngle=b,expectedPulse=pulse,durationSeconds=.5,lease=LEASE)
            validate(body)
            for seconds in (.3,1,2,5,20,True):
                with self.assertRaises(ValueError):validate(dict(body,durationSeconds=seconds))

    def test_each_leg_is_25_frames_including_preparation(self):
        for flags,a,b,old in [({},12.3,45.5,800),({'second':True},3,33,700),({'third':True},30,60,1000)]:
            sweep=FirstSweep(old,dict(startAngle=a,endAngle=b,durationSeconds=.5),**flags)
            self.assertEqual(len(sweep.steps),50)
            self.assertEqual(sum(p for _,_,p in sweep.steps),25)

    def test_third_local_path_is_bounded_and_releases(self):
        now=[0.0];driver=Driver();servo=Servos(driver,clock=lambda:now[0],first_manual=True)
        servo.command(dict(action='arm_third',angle=30))
        body=dict(action='smooth_third',startAngle=30,endAngle=60,expectedPulse=833,durationSeconds=.5,lease=LEASE)
        servo.command(body)
        with self.assertRaises(ValueError):servo.command(body)
        for i in range(25):
            now[0]+=.020001;servo.tick()
        self.assertEqual(servo.widths[2],1167)
        self.assertIsNone(servo.sweeps[2])
        self.assertEqual(servo.widths[:2],[0,0])
        self.assertTrue(all(pin==22 and 833<=width<=1167 for pin,width in driver.calls))
        now[0]+=.51;servo.tick()
        self.assertEqual(driver.calls[-1],(22,0))
        self.assertFalse(servo.active[2])

    def test_third_stop_never_replays(self):
        now=[0.0];d=Driver();s=Servos(d,clock=lambda:now[0],first_manual=True)
        s.command(dict(action='arm_third',angle=60))
        s.command(dict(action='smooth_third',startAngle=60,endAngle=30,expectedPulse=1167,durationSeconds=.5,lease=LEASE))
        s.command(dict(action='release',channel=3))
        calls=list(d.calls)
        now[0]=10;s.tick()
        self.assertEqual(d.calls,calls)
