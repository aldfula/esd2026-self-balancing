#!/usr/bin/env python3
"""Collect a stationary, sensor-only MPU6050 CSV and a .csv.json run record.

Run from the repository root: python3 -m tools.collect_imu --output logs/imu.csv
Requires smbus2 on the Pi. Motor power must be disconnected by the student.
Body tilt, body gyro rate, wheel speeds and motor outputs are unmeasured (empty).
--vbat is an optional manually measured pre-run voltage, not a live ADC reading.
"""

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time

from src.sensing.imu_calib import RAW_FIELDS, UNIT_FIELDS
from src.sensing.mpu6050 import MPU6050, SAMPLE_RATE_HZ

FIELDS = ("t", "tick", "theta_deg", "gyro_dps", "wheel_l", "wheel_r",
          "u_l", "u_r", "vbat", "i2c_err", "valid", "error", "read_s",
          *RAW_FIELDS, *UNIT_FIELDS)


class CSVWriter:
    """Bounded, nonblocking producer; disk writes occur on a worker thread."""
    def __init__(self, stream):
        self.queue = queue.Queue(maxsize=128)
        self.error = None
        self.rows = 0
        self.thread = threading.Thread(target=self._write, args=(stream,))
        self.thread.start()

    def _write(self, stream):
        try:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            while True:
                row = self.queue.get()
                if row is None:
                    break
                writer.writerow(row)
                self.rows += 1
            stream.flush()
        except Exception as exc:
            self.error = exc

    def put(self, row):
        if self.error:
            raise OSError(f"CSV writer failed: {self.error}")
        try:
            self.queue.put_nowait(row)
        except queue.Full as exc:
            raise OSError("CSV queue full; acquisition stopped") from exc

    def close(self):
        while self.thread.is_alive():
            try:
                self.queue.put(None, timeout=0.1)
                break
            except queue.Full:
                pass
        self.thread.join()
        if self.error:
            raise OSError(f"CSV writer failed: {self.error}")


def collect(sensor, stream, seconds=30.0, vbat=None, *,
            clock=time.monotonic, sleep=time.sleep, metadata=None):
    """Return a run record; any fault stops at its first occurrence."""
    if not math.isfinite(seconds) or seconds < 2 / SAMPLE_RATE_HZ:
        raise ValueError("duration must allow at least two samples")
    if vbat is not None and (not math.isfinite(vbat) or vbat <= 0):
        raise ValueError("manual battery voltage must be positive and finite")
    target = math.ceil(seconds * SAMPLE_RATE_HZ)
    result = metadata if metadata is not None else {}
    result.update(schema="mpu6050-sensor-v1", status="running", reason="",
                  rate_hz=SAMPLE_RATE_HZ, seconds=seconds, target_samples=target,
                  samples=0, i2c_err=0, sensor_rate_hz=100.0,
                  axis_frame="sensor_uncalibrated", motor_interface="none",
                  vbat=vbat, vbat_source="manual_pre_run" if vbat else "unmeasured")
    writer = CSVWriter(stream)
    previous = None
    try:
        result["registers_start"] = sensor.configure(sleep=sleep)
        start = clock()
        for tick in range(target):
            delay = start + tick / SAMPLE_RATE_HZ - clock()
            if delay > 0:
                sleep(delay)
            before = clock()
            row = {"tick": tick, "vbat": vbat, "i2c_err": result["i2c_err"],
                   "valid": 1, "error": ""}
            try:
                row.update(sensor.read())
            except (OSError, ValueError) as exc:
                if isinstance(exc, OSError):
                    result["i2c_err"] += 1
                row.update(valid=0, i2c_err=result["i2c_err"],
                           error=f"{type(exc).__name__}: {exc}")
            now = clock()
            row.update(t=now - start, read_s=now - before)
            if row["valid"] and (row["read_s"] >= 1.5 / SAMPLE_RATE_HZ or
                    (previous is None and row["t"] >= 1.5 / SAMPLE_RATE_HZ) or
                    row["t"] >= seconds or
                    (previous is not None and
                     (now <= previous or now - previous >= 1.5 / SAMPLE_RATE_HZ))):
                row.update(valid=0, error="host timing violation")
            writer.put(row)
            previous = now
            if not row["valid"]:
                raise ValueError(row["error"])
        result["registers_end"] = sensor.verify_settings()
        result["status"] = "complete"
    except KeyboardInterrupt:
        result.update(status="void", reason="interrupted by user")
    except Exception as exc:
        result.update(status="void", reason=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            writer.close()
        except OSError as exc:
            result.update(status="void", reason=str(exc))
        result["samples"] = writer.rows
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda value: int(value, 0),
                        choices=(0x68, 0x69), default=0x68)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--vbat", type=float, help="manually measured pre-run battery V")
    args = parser.parse_args()
    if args.bus < 0 or not math.isfinite(args.seconds) or args.seconds < 2 / SAMPLE_RATE_HZ:
        parser.error("bus must be nonnegative and duration must allow two samples")
    if args.vbat is not None and (not math.isfinite(args.vbat) or args.vbat <= 0):
        parser.error("--vbat must be positive and finite")
    if args.output.suffix != ".csv":
        parser.error("--output must end in .csv")
    record = {"status": "running", "started_at": datetime.now().astimezone().isoformat(),
              "bus": args.bus, "address": args.address}
    try:
        from smbus2 import SMBus
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves all earlier logs, including failed runs.
        with args.output.open("x", newline="") as stream, \
                Path(str(args.output) + ".json").open("x") as sidecar:
            json.dump(record, sidecar, indent=2)
            sidecar.flush()
            try:
                with SMBus(args.bus) as bus:
                    collect(MPU6050(bus, args.address), stream, args.seconds, args.vbat,
                            metadata=record)
            except (OSError, ValueError) as exc:
                record.update(status="void", reason=f"{type(exc).__name__}: {exc}")
            sidecar.seek(0)
            json.dump(record, sidecar, indent=2, allow_nan=False)
            sidecar.write("\n")
            sidecar.truncate()
    except (ImportError, OSError) as exc:
        print(f"VOID: {exc}", file=sys.stderr)
        return 1
    print(f"{record['status'].upper()}: {args.output} "
          f"samples={record.get('samples', 0)} reason={record.get('reason', '')}")
    return 0 if record["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
