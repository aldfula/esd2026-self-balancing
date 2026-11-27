#!/usr/bin/env python3
"""Collect a stationary, sensor-only MPU6050 CSV and a .csv.json run record.

Run from the repository root: python3 -m tools.collect_imu --output logs/imu.csv
Requires smbus2 on the Pi. Motor power must be disconnected by the student.
Body tilt, body gyro rate, wheel speeds and motor outputs are unmeasured (empty).
--vbat is an optional manually measured pre-run voltage, not a live ADC reading.
"""

import argparse
from collections import deque
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time

from src.sensing.imu_calib import (RAW_FIELDS, UNIT_FIELDS, CORRECTED_FIELDS,
                                   apply_gyro_bias, load_gyro_bias, validate_gyro_bias)
from src.sensing.mpu6050 import MPU6050, SAMPLE_RATE_HZ

FIELDS = ("t", "tick", "theta_deg", "gyro_dps", "wheel_l", "wheel_r",
          "u_l", "u_r", "vbat", "i2c_err", "valid", "error", "read_s",
          *RAW_FIELDS, *UNIT_FIELDS)


class CSVWriter:
    """Bounded, nonblocking producer; disk writes occur on a worker thread."""
    def __init__(self, stream, *, diagnostics=False, fields=FIELDS):
        self.fields = fields
        self.queue = queue.Queue(maxsize=128)
        self.error = None
        self.rows = 0
        self.diagnostics = diagnostics
        if diagnostics:
            self.origin = time.monotonic()
            self.history = deque(maxlen=128)
            self.queue_high_water = 0
            self.progress = {"operation": "starting", "tick": None, "started_s": 0.0}
            self.timings = {name: {"count": 0, "last_wall_s": 0.0,
                                  "max_wall_s": 0.0, "last_thread_cpu_s": 0.0,
                                  "max_thread_cpu_s": 0.0, "last_tick": None,
                                  "max_wall_tick": None}
                            for name in ("header", "row", "flush")}
            self.first_fault = None
            self.writer_error = None
        self.thread = threading.Thread(target=self._write, args=(stream,))
        self.thread.start()

    def _begin(self, operation, tick=None):
        # Only the worker writes progress. Publish a new object for each phase.
        wall = time.monotonic()
        self.progress = {"operation": operation, "tick": tick,
                         "started_s": wall - self.origin}
        return wall, time.thread_time()

    def _finish(self, operation, started):
        wall = time.monotonic() - started[0]
        cpu = time.thread_time() - started[1]
        previous = self.timings[operation]
        self.timings[operation] = {
            "count": previous["count"] + 1,
            "last_wall_s": wall, "max_wall_s": max(previous["max_wall_s"], wall),
            "last_thread_cpu_s": cpu,
            "max_thread_cpu_s": max(previous["max_thread_cpu_s"], cpu),
            "last_tick": self.progress["tick"],
            "max_wall_tick": (self.progress["tick"] if wall > previous["max_wall_s"]
                              else previous["max_wall_tick"]),
        }

    def _snapshot(self, event, tick=None):
        progress = self.progress
        now = time.monotonic() - self.origin
        size = self.queue.qsize()
        self.queue_high_water = max(self.queue_high_water, size)
        return {"event": event, "tick": tick, "t_s": now,
                "queue_size_approx": size, "rows_completed": self.rows,
                "last_row_write_wall_s": self.timings["row"]["last_wall_s"],
                "worker": dict(progress, elapsed_s=max(0.0, now - progress["started_s"]))}

    def _fault(self, event, tick):
        if self.diagnostics and self.first_fault is None:
            self.first_fault = self._snapshot(event, tick)

    def report(self):
        """Read after close; fault/history snapshots retain the pre-drain state."""
        final = self._snapshot("closed")
        return {"clock": "monotonic_seconds_since_writer_creation",
                "queue_capacity": self.queue.maxsize,
                "queue_high_water_observed": self.queue_high_water,
                "history_limit": self.history.maxlen,
                "recent_enqueues": list(self.history),
                "first_fault": self.first_fault,
                "writer_error": self.writer_error,
                "operation_timings": self.timings,
                "final": final}

    def _write(self, stream):
        try:
            writer = csv.DictWriter(stream, fieldnames=self.fields)
            if self.diagnostics:
                started = self._begin("header")
            writer.writeheader()
            if self.diagnostics:
                self._finish("header", started)
            while True:
                if self.diagnostics:
                    self._begin("waiting_for_row")
                row = self.queue.get()
                if row is None:
                    break
                if self.diagnostics:
                    started = self._begin("row", row["tick"])
                writer.writerow(row)
                self.rows += 1
                if self.diagnostics:
                    self._finish("row", started)
            if self.diagnostics:
                started = self._begin("flush")
            stream.flush()
            if self.diagnostics:
                self._finish("flush", started)
                self._begin("done")
        except Exception as exc:
            if self.diagnostics:
                self.writer_error = {"type": type(exc).__name__, "message": str(exc),
                                     "errno": getattr(exc, "errno", None),
                                     "worker": self.progress}
                self._begin("error")
            self.error = exc

    def put(self, row):
        if self.error:
            self._fault("writer_error", row["tick"])
            raise OSError(f"CSV writer failed: {self.error}")
        try:
            self.queue.put_nowait(row)
        except queue.Full as exc:
            self._fault("queue_full", row["tick"])
            raise OSError("CSV queue full; acquisition stopped") from exc
        if self.diagnostics:
            self.history.append(self._snapshot("enqueued", row["tick"]))

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
            clock=time.monotonic, sleep=time.sleep, metadata=None,
            writer_diagnostics=False, gyro_bias=None):
    """Return a run record; any fault stops at its first occurrence."""
    if not math.isfinite(seconds) or seconds < 2 / SAMPLE_RATE_HZ:
        raise ValueError("duration must allow at least two samples")
    if vbat is not None and (not math.isfinite(vbat) or vbat <= 0):
        raise ValueError("manual battery voltage must be positive and finite")
    bias = validate_gyro_bias(gyro_bias) if gyro_bias is not None else None
    target = math.ceil(seconds * SAMPLE_RATE_HZ)
    result = metadata if metadata is not None else {}
    if "gyro_bias" in result:
        raise ValueError("run metadata already contains gyro bias state")
    result.update(schema="mpu6050-sensor-v1", status="running", reason="",
                  rate_hz=SAMPLE_RATE_HZ, seconds=seconds, target_samples=target,
                  samples=0, i2c_err=0, sensor_rate_hz=100.0,
                  axis_frame="sensor_uncalibrated", motor_interface="none",
                  vbat=vbat, vbat_source="manual_pre_run" if vbat else "unmeasured")
    if bias is not None:
        result.update(schema="mpu6050-sensor-v2",
                      gyro_bias={"applied": True, "config": bias})
    fields = FIELDS + CORRECTED_FIELDS if bias is not None else FIELDS
    writer = CSVWriter(stream, diagnostics=writer_diagnostics, fields=fields)
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
                if bias is not None:
                    row = apply_gyro_bias(row, bias)
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
        if writer_diagnostics:
            result["writer_diagnostics"] = writer.report()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda value: int(value, 0),
                        choices=(0x68, 0x69), default=0x68)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--vbat", type=float, help="manually measured pre-run battery V")
    parser.add_argument("--writer-diagnostics", action="store_true",
                        help="save bounded in-memory CSV writer diagnostics in the run JSON")
    parser.add_argument("--gyro-bias", type=Path, metavar="CONFIG",
                        help="explicitly apply sensor-axis gyro bias from this JSON file")
    args = parser.parse_args()
    if args.bus < 0 or not math.isfinite(args.seconds) or args.seconds < 2 / SAMPLE_RATE_HZ:
        parser.error("bus must be nonnegative and duration must allow two samples")
    if args.vbat is not None and (not math.isfinite(args.vbat) or args.vbat <= 0):
        parser.error("--vbat must be positive and finite")
    if args.output.suffix != ".csv":
        parser.error("--output must end in .csv")
    try:
        bias = load_gyro_bias(args.gyro_bias) if args.gyro_bias is not None else None
    except (OSError, ValueError, OverflowError) as exc:
        parser.error(f"--gyro-bias: {exc}")
    record = {"status": "running", "started_at": datetime.now().astimezone().isoformat(),
              "bus": args.bus, "address": args.address}
    if args.gyro_bias is not None:
        record["gyro_bias_config_path"] = str(args.gyro_bias)
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
                            metadata=record, writer_diagnostics=args.writer_diagnostics,
                            gyro_bias=bias)
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
