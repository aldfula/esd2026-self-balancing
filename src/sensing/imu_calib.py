"""MPU6050 raw-to-unit conversion, in sensor axes only.

Source: InvenSense RM-MPU-6000A-00 rev. 4.2, sections 4.17-4.19:
https://www.sigmaelectronica.net/wp-content/uploads/2025/08/MPU-6000.pdf
No body-axis mapping, mounting offset, or gyro bias has been measured yet.
Do not use these uncalibrated values as a robot tilt estimate.
"""

import struct

RAW_FIELDS = ("ax_raw", "ay_raw", "az_raw", "temp_raw", "gx_raw", "gy_raw", "gz_raw")
UNIT_FIELDS = ("ax_g", "ay_g", "az_g", "temp_c", "gx_dps", "gy_dps", "gz_dps")


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
