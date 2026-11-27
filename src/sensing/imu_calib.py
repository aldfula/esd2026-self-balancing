"""MPU6050 raw-to-unit conversion, in sensor axes only.

Source: InvenSense RM-MPU-6000A-00 rev. 4.2, sections 4.17-4.19:
https://www.sigmaelectronica.net/wp-content/uploads/2025/08/MPU-6000.pdf
Optional stationary gyro bias correction remains in sensor axes.
No body-axis mapping or upright offset has been measured.
Do not use these sensor values as a robot tilt estimate.
"""

from copy import deepcopy
import json
import math
import struct

RAW_FIELDS = ("ax_raw", "ay_raw", "az_raw", "temp_raw", "gx_raw", "gy_raw", "gz_raw")
UNIT_FIELDS = ("ax_g", "ay_g", "az_g", "temp_c", "gx_dps", "gy_dps", "gz_dps")
CORRECTED_FIELDS = tuple(f"g{axis}_corrected_dps" for axis in "xyz")


def validate_gyro_bias(config):
    """Validate and copy an explicit sensor-axis calibration snapshot."""
    expected = {"schema", "axis_frame", "units", "operation", "bias_dps", "evidence"}
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("invalid gyro bias configuration fields")
    for name, value in (("schema", "mpu6050-gyro-bias-v1"),
                        ("axis_frame", "sensor"), ("units", "deg/s"),
                        ("operation", "g_corrected = g - b")):
        if config[name] != value:
            raise ValueError(f"invalid gyro bias {name}")
    bias = config["bias_dps"]
    if not isinstance(bias, dict) or set(bias) != set("xyz"):
        raise ValueError("gyro bias requires exactly x, y, z")
    for value in bias.values():
        if type(value) not in (int, float) or abs(value) > 250 or not math.isfinite(value):
            raise ValueError("gyro bias must be finite numeric deg/s within sensor range")
    evidence = config["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {
            "source_log", "validation_record", "adoption_commit", "scope"}:
        raise ValueError("invalid gyro bias evidence fields")
    if any(not isinstance(value, str) or not value.strip() for value in evidence.values()):
        raise ValueError("gyro bias evidence must contain nonempty strings")
    sha = evidence["adoption_commit"]
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise ValueError("gyro bias adoption_commit must be a full commit SHA")
    return deepcopy(config)


def load_gyro_bias(path):
    """Read once before opening I2C or output files; reject ambiguous JSON."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate gyro bias configuration key: {key}")
            result[key] = value
        return result

    with open(path) as stream:
        return validate_gyro_bias(json.load(stream, object_pairs_hook=unique_object))


def apply_gyro_bias(sample, config):
    """Return an additive correction; never overwrite raw/pre-correction data."""
    if any(name in sample for name in CORRECTED_FIELDS):
        raise ValueError("gyro bias correction already present")
    corrected = {}
    for axis, name in zip("xyz", CORRECTED_FIELDS):
        value = sample[f"g{axis}_dps"]
        if not math.isfinite(value):
            raise ValueError("nonfinite gyro sample")
        corrected[name] = value - config["bias_dps"][axis]
    return dict(sample, **corrected)


def convert_sample(block):
    """Decode one coherent 14-byte burst at +/-2 g and +/-250 deg/s."""
    if len(block) != 14:
        raise ValueError("expected exactly 14 sensor bytes")
    raw = struct.unpack(">7h", bytes(block))
    values = dict(zip(RAW_FIELDS, raw))
    for axis, value in zip("xyz", raw[:3]):
        values[f"a{axis}_g"] = value / 16384.0
    values["temp_c"] = raw[3] / 340.0 + 36.53
    for axis, value in zip("xyz", raw[4:]):
        values[f"g{axis}_dps"] = value / 131.0
    return values
