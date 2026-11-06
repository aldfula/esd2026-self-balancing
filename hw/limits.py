"""Limits for the two-wheel DC-motor robot (AGENTS.md sections 3 and 8).

Pure calculations only: the motor driver must apply the returned commands.
Signed duty is normalized to [-1, 1]; hardware wiring determines direction.
Changes to limits require student approval and a documented hardware test.
"""

import math

# Source: AGENTS.md section 8-B. This is a cutoff, not an operating target.
MAX_TILT_DEG = 35.0

# Output is disabled until a student-approved, tested duty cap is configured.
# The starter's 0.6 was an unverified example, not a measured motor limit.
MOTOR_MAX_DUTY = 0.0


def clamp(value, low, high, name="command"):
    """Return (bounded value, clipped); reject non-finite input and bounds."""
    if not all(math.isfinite(item) for item in (value, low, high)):
        raise ValueError(f"{name}: value and bounds must be finite")
    if low > high:
        raise ValueError(f"{name}: low limit {low} is above high limit {high}")
    if value < low:
        return low, True
    if value > high:
        return high, True
    return value, False


def limit_motor_commands(left, right, theta_deg, *, sensor_ok, enabled=False):
    """Return (left duty, right duty, stopped) without accessing hardware.

    theta_deg must be calibrated body tilt. sensor_ok must reflect a fresh,
    valid IMU sample, including I2C errors; this function cannot detect stale
    samples itself. The caller must latch a fall/fault and require a deliberate
    re-arm, handle driver faults/watchdogs and battery protection separately.
    A stopped result requests zero duty, not a hardware-specific brake mode.
    """
    if enabled is not True or sensor_ok is not True:
        return 0.0, 0.0, True
    try:
        finite = all(math.isfinite(value) for value in
                     (left, right, theta_deg, MOTOR_MAX_DUTY, MAX_TILT_DEG))
    except (TypeError, ValueError, OverflowError):
        return 0.0, 0.0, True
    if not finite:
        return 0.0, 0.0, True
    if not 0.0 < MOTOR_MAX_DUTY <= 1.0 or MAX_TILT_DEG <= 0.0:
        return 0.0, 0.0, True
    if abs(theta_deg) > MAX_TILT_DEG:
        return 0.0, 0.0, True
    left, _ = clamp(left, -MOTOR_MAX_DUTY, MOTOR_MAX_DUTY, "left duty")
    right, _ = clamp(right, -MOTOR_MAX_DUTY, MOTOR_MAX_DUTY, "right duty")
    return left, right, False
