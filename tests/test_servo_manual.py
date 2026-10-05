import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / 'pi'))
from servos import Servos, validate
from servo_pwm import KernelPwmDriver

class Driver:
    name = 'kernel-pwm'
    def __init__(self): self.calls = []
    def pulse(self, pin, width):
        KernelPwmDriver._validate(pin, width)
        self.calls.append((pin, width))
    def tick(self): pass
    def extend(self, pin, width): self.calls.append(('extend', pin, width))

class ManualTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.driver = Driver()
        self.s = Servos(self.driver, clock=lambda:self.now, first_manual=True)
    def arm(self): self.s.command({'action':'arm_first'})
    def step(self, delta, expected=None):
        return self.s.command({'action':'step_first', 'delta':delta, 'expectedPulse':self.s.widths[0] if expected is None else expected})
    def test_start_status_arm_stop_do_not_emit_position(self):
        self.s.command({'action':'status'})
        self.s.tick()
        self.arm()
        self.s.command({'action':'stop'})
        self.assertEqual(self.driver.calls, [])
        self.assertEqual(self.s.widths[0], 1000)
        self.assertFalse(self.s.active[0])
    def test_steps_bounds_expiry_and_no_replay(self):
        with self.assertRaises(ValueError): self.step(-1, 1000)
        self.arm()
        self.step(-1,1000)
        self.assertEqual(self.driver.calls, [(17,989)])
        with self.assertRaises(ValueError): self.step(-1,1000)
        self.now += .6
        self.s.tick()
        self.assertEqual(self.driver.calls[-1], (17,0))
        self.s.command({'action':'keepalive','lease':'6e552c68-1943-4e06-8203-caa37ae35573'})
        self.assertFalse(self.s.active[0])
        widths=[989]
        while self.s.widths[0]>637:
            self.step(-1);widths.append(self.s.widths[0])
        self.assertTrue(all(0 < a-b <=11 for a,b in zip(widths,widths[1:])))
        with self.assertRaises(ValueError): self.step(-1)
        while self.s.widths[0]<1005: self.step(1)
        with self.assertRaises(ValueError): self.step(1)
        self.assertTrue(all(pin==17 and (width==0 or 637<=width<=1005) for pin,width in self.driver.calls))
    def test_legacy_first_move_and_sweep_rejected_without_output(self):
        for angle in [45,60,90,135]:
            with self.assertRaises(ValueError):
                self.s.command({'action':'move','channel':1,'angle':angle,'wide':True})
        with self.assertRaises(ValueError):
            self.s.command({'action':'sweep','channel':1,'startAngle':45,'endAngle':90,'durationSeconds':5,'wide':True,'lease':'6e552c68-1943-4e06-8203-caa37ae35573'})
        self.assertEqual(self.driver.calls,[])
    def test_other_channels_require_explicit_calibration(self):
        for channel in (2,3):
            with self.assertRaises(ValueError):
                self.s.command({'action':'move','channel':channel,'angle':45})
        self.assertEqual(self.driver.calls, [])

    def test_third_arm_endpoints_expiry_and_isolation(self):
        with self.assertRaises(ValueError):
            self.s.command({'action':'move_third','angle':45,'expectedPulse':1000})
        self.s.command({'action':'arm_third','angle':45})
        self.assertEqual(self.driver.calls, [])
        with self.assertRaises(ValueError):
            self.s.command({'action':'arm_third','angle':45})
        for angle, pulse in ((30,833),(60,1167),(45,1000)):
            self.s.command({'action':'move_third','angle':angle,'expectedPulse':self.s.widths[2]})
            self.assertEqual(self.driver.calls[-1],(22,pulse))
        with self.assertRaises(ValueError):
            self.s.command({'action':'move_third','angle':45,'expectedPulse':1167})
        self.now += .6
        self.s.tick()
        self.assertEqual(self.driver.calls[-1],(22,0))
        self.assertFalse(self.s.active[2])
        self.s.command({'action':'status'})
        self.s.tick()
        self.assertEqual(self.s.widths[:2],[0,0])
        self.assertTrue(all(pin==22 for pin,width in self.driver.calls))

    def test_third_invalid_angles_and_schema(self):
        for action in ('arm_third','move_third'):
            for angle in (0,29.9,60.1,180,True,float('nan'),float('inf'),'90'):
                body={'action':action,'angle':angle}
                if action=='move_third': body['expectedPulse']=1000
                with self.assertRaises(ValueError): validate(body)
        for pulse in (500,832,1168,2500,True,1000.0):
            with self.assertRaises(ValueError):
                validate({'action':'move_third','angle':45,'expectedPulse':pulse})
        self.assertEqual(self.driver.calls, [])
    def test_invalid_steps_and_rearm_rejected(self):
        for delta in [True,0,2,-2,1.0]:
            with self.assertRaises(ValueError):validate({'action':'step_first','delta':delta,'expectedPulse':1000})
        for pulse in [True,499,636,1006,1000.0]:
            with self.assertRaises(ValueError):validate({'action':'step_first','delta':-1,'expectedPulse':pulse})
        self.arm();self.step(-1)
        with self.assertRaises(ValueError):self.arm()
        fresh=Servos(Driver(),first_manual=True)
        self.assertEqual(fresh.widths[0],0)

if __name__=='__main__': unittest.main()
