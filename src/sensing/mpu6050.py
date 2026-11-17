"""Sensor-only I2C access; no GPIO or motor interfaces.

Registers follow InvenSense RM-MPU-6000A-00 rev. 4.2.
The device runs at 100 Hz; the host logs the latest sample at 30 Hz.
DLPF_CFG=5 (10 Hz) is an initial acquisition setting, not a tuned filter.
"""

import time

from .imu_calib import convert_sample

SAMPLE_RATE_HZ = 30.0
REGISTERS = {
    "PWR_MGMT_1": (0x6B, 0x01),
    "PWR_MGMT_2": (0x6C, 0x00),
    "SMPLRT_DIV": (0x19, 9),
    "CONFIG": (0x1A, 5),
    "GYRO_CONFIG": (0x1B, 0),
    "ACCEL_CONFIG": (0x1C, 0),
    "FIFO_EN": (0x23, 0),
    "USER_CTRL": (0x6A, 0),
    "INT_PIN_CFG": (0x37, 0),
    "INT_ENABLE": (0x38, 1),
}


class MPU6050:
    def __init__(self, bus, address=0x68):
        if address not in (0x68, 0x69):
            raise ValueError("MPU6050 address must be 0x68 or 0x69")
        self.bus = bus
        self.address = address

    def verify_settings(self):
        actual = {name: self.bus.read_byte_data(self.address, reg)
                  for name, (reg, _) in REGISTERS.items()}
        actual["WHO_AM_I"] = self.bus.read_byte_data(self.address, 0x75)
        expected = {name: value for name, (_, value) in REGISTERS.items()}
        expected["WHO_AM_I"] = 0x68  # Independent of the AD0 address bit.
        if actual != expected:
            raise ValueError(f"sensor register readback mismatch: {actual!r}")
        return actual

    def configure(self, sleep=time.sleep):
        if self.bus.read_byte_data(self.address, 0x75) != 0x68:
            raise ValueError("WHO_AM_I is not 0x68; stop and check the device")
        self.bus.write_byte_data(self.address, 0x6B, 0x80)
        sleep(0.1)
        for name, (reg, value) in REGISTERS.items():
            self.bus.write_byte_data(self.address, reg, value)
            if name == "PWR_MGMT_1":
                sleep(0.1)
        actual = self.verify_settings()
        self.bus.read_byte_data(self.address, 0x3A)
        sleep(0.1)  # Settle and wait for fresh data before starting host timing.
        return actual

    def read(self):
        if not self.bus.read_byte_data(self.address, 0x3A) & 1:
            raise ValueError("no fresh DATA_RDY sample")
        return convert_sample(self.bus.read_i2c_block_data(self.address, 0x3B, 14))
