"""Calculation tests only; no motor driver or hardware is accessed."""
import math
import unittest
from unittest.mock import patch

from hw import limits


class LimitsTests(unittest.TestCase):
    def commands(self, left=0.1, right=-0.1, tilt=0.0, **kwargs):
        return limits.limit_motor_commands(left, right, tilt,
                                          sensor_ok=kwargs.get('sensor_ok', True),
                                          enabled=kwargs.get('enabled', True))

    def test_default_configuration_disables_both_motors(self):
        self.assertEqual(self.commands(), (0.0, 0.0, True))

    @patch.object(limits, 'MOTOR_MAX_DUTY', 0.2)  # Test fixture, not a hardware recommendation.
    def test_direction_and_independent_saturation(self):
        self.assertEqual(self.commands(0.8, -0.1), (0.2, -0.1, False))
        self.assertEqual(self.commands(-0.8, 0.9), (-0.2, 0.2, False))

    @patch.object(limits, 'MOTOR_MAX_DUTY', 0.2)
    def test_rule_boundary_and_both_fall_directions(self):
        for tilt in (-35.0, 35.0):
            self.assertFalse(self.commands(tilt=tilt)[2])
        for tilt in (-35.001, 35.001):
            self.assertEqual(self.commands(tilt=tilt), (0.0, 0.0, True))

    @patch.object(limits, 'MOTOR_MAX_DUTY', 0.2)
    def test_fault_and_disarm_stop_both_motors(self):
        self.assertEqual(self.commands(sensor_ok=False), (0.0, 0.0, True))
        self.assertEqual(self.commands(enabled=False), (0.0, 0.0, True))
        self.assertEqual(limits.limit_motor_commands(0.1, 0.1, 0, sensor_ok=True),
                         (0.0, 0.0, True))

    @patch.object(limits, 'MOTOR_MAX_DUTY', 0.2)
    def test_nonfinite_commands_and_sensor_fail_closed(self):
        for bad in (math.nan, math.inf, -math.inf):
            for values in ((bad, 0, 0), (0, bad, 0), (0, 0, bad)):
                self.assertEqual(self.commands(*values), (0.0, 0.0, True))

    @patch.object(limits, 'MOTOR_MAX_DUTY', 0.2)
    def test_malformed_sample_and_flags_fail_closed(self):
        for bad in (None, 'invalid', 10**1000):
            self.assertEqual(self.commands(tilt=bad), (0.0, 0.0, True))
        for bad in ('false', 1, None):
            self.assertEqual(self.commands(sensor_ok=bad), (0.0, 0.0, True))
            self.assertEqual(self.commands(enabled=bad), (0.0, 0.0, True))

    def test_invalid_cap_fails_closed(self):
        for cap in (-0.1, 1.1, math.nan, math.inf):
            with patch.object(limits, 'MOTOR_MAX_DUTY', cap):
                self.assertEqual(self.commands(), (0.0, 0.0, True))

    def test_clamp_rejects_invalid_inputs(self):
        for values in ((math.nan, -1, 1), (0, -math.inf, 1), (0, 1, -1)):
            with self.assertRaises(ValueError):
                limits.clamp(*values)
        self.assertEqual(limits.clamp(0.5, -1, 1), (0.5, False))


if __name__ == '__main__':
    unittest.main()
