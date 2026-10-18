import math
import unittest

from echograph.motion.curve_interpolation import curve_controls, curve_value


class CurveInterpolationTests(unittest.TestCase):
    def test_vertical_weighted_tangents_follow_the_drawn_cubic_without_infinite_values(self):
        first = dict(mode='tied',weighted=['in','out'],**{'in':[0.,-3.],'out':[0.,3.]})
        last = dict(mode='untied',weighted=['in'],**{'in':[0.,-2.],'out':[.3,0.]})
        self.assertEqual(curve_controls(0.,1.,0.,1.,first,last),[(0.,3.),(1.,-1.)])
        for t in (1e-4,.1,.25,.5,.75,.9,.9999):
            x = 3*(1-t)*t*t+t*t*t
            expected = 9*(1-t)*(1-t)*t-3*(1-t)*t*t+t*t*t
            actual = curve_value(0.,1.,0.,1.,x,first,last)
            self.assertTrue(math.isfinite(actual))
            self.assertAlmostEqual(actual,expected,places=8)
        self.assertEqual(curve_value(0.,1.,0.,1.,0.,first,last),0.)
        self.assertEqual(curve_value(0.,1.,0.,1.,1.,first,last),1.)

    def test_old_slope_tangents_keep_their_saved_interpolation(self):
        first = dict(mode='tied',**{'out':[.2,.7]})
        last = dict(mode='untied',**{'in':[-.5,.3]})
        for u in (.01,.1,.5,.9,.99):
            expected = (2*u**3-3*u**2+1)*4+(u**3-2*u**2+u)*2*3.5+(-2*u**3+3*u**2)*7+(u**3-u**2)*2*(-.6)
            self.assertAlmostEqual(curve_value(5.,7.,4.,7.,5+2*u,first,last),expected,places=11)

    def test_stretching_vertical_tangents_changes_values_and_scales_with_the_clock(self):
        first = dict(mode='untied',weighted=['out'],**{'in':[-.2,0.],'out':[0.,1.]})
        short = curve_value(0.,1.,0.,0.,.2,first)
        first['out'][1] = 2.
        tall = curve_value(0.,1.,0.,0.,.2,first)
        self.assertAlmostEqual(tall,2*short)
        self.assertAlmostEqual(curve_value(1000.,1024.,0.,0.,1004.8,first),tall)

    def test_overlapping_control_handles_follow_their_actual_cubic(self):
        first = dict(mode='untied',weighted=['out'],**{'out':[.8,2.]})
        last = dict(mode='untied',weighted=['in'],**{'in':[-.8,-2.]})
        controls = curve_controls(0.,1.,0.,1.,first,last)
        self.assertAlmostEqual(controls[0][0],.8)
        self.assertAlmostEqual(controls[1][0],.2)
        for t in (.01,.1,.25,.45,.5,.55,.75,.9,.99):
            x = 3*(1-t)**2*t*.8+3*(1-t)*t*t*.2+t**3
            y = 3*(1-t)**2*t*2.-3*(1-t)*t*t+t**3
            self.assertAlmostEqual(curve_value(0.,1.,0.,1.,x,first,last),y,places=9)

    def test_very_long_handles_keep_their_vectors_and_tangent_direction_without_time_loops(self):
        first = dict(mode='tied',weighted=['out'],**{'out':[200.,400.]})
        last = dict(mode='untied',weighted=['in'],**{'in':[-300.,-900.]})
        self.assertEqual(curve_controls(0.,1.,0.,1.,first,last),[(1.,2.),(0.,-2.)])
        self.assertEqual(first['out'],[200.,400.])
        self.assertEqual(last['in'],[-300.,-900.])
        for t in (.01,.1,.25,.4,.5,.6,.75,.9,.99):
            x = 3*(1-t)**2*t+t**3
            y = 6*(1-t)**2*t-6*(1-t)*t*t+t**3
            self.assertAlmostEqual(curve_value(0.,1.,0.,1.,x,first,last),y,places=9)

    def test_fully_crossed_time_handles_remain_precise_near_the_flat_time_derivative(self):
        first = dict(mode='untied',weighted=['out'],**{'out':[1.,2.]})
        last = dict(mode='untied',weighted=['in'],**{'in':[-1.,-3.]})
        for t in (.499,.4999,.49999,.5,.50001,.5001,.501):
            x = .5+4*(t-.5)**3
            expected = 6*(1-t)**2*t-6*(1-t)*t*t+t**3
            self.assertAlmostEqual(curve_value(0.,1.,0.,1.,x,first,last),expected,places=7)


if __name__ == '__main__':
    unittest.main()
