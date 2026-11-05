"""Actuator limits — the only place these numbers live (AGENTS.md section 3-2).

Changing any value here needs an approved plan and a `rules:` or `hw:` commit
that says which value moved from what to what. Keep the mechanical stops
slightly outside these software limits so the software limit is hit first.

The values below are placeholders. Measure your own rig and replace them.
"""

# Servo / motor command limits (fill in from your rig, with the source of each number)
SERVO_MIN_DEG = -10.0   # e.g. measured linkage clearance minus margin
SERVO_MAX_DEG = 10.0
MOTOR_MAX_DUTY = 0.6    # fraction of full PWM duty for first runs; raise only after an approved plan

# Seconds a command may sit at a limit before the output returns to neutral (stall guard)
STALL_TIMEOUT_S = 2.0


def clamp(value, low, high, name="command"):
    """Clip a command into [low, high] and report whether it was clipped.

    Returns (clipped_value, was_clipped). Log was_clipped: a controller that
    keeps hitting its limits is saturating, and the log should show it.
    """
    if low > high:
        raise ValueError(f"{name}: low limit {low} is above high limit {high}")
    if value < low:
        return low, True
    if value > high:
        return high, True
    return value, False
