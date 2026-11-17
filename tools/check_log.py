#!/usr/bin/env python3
"""Check a control-loop CSV log before anyone analyses it (AGENTS.md section 4-5).

A log passes only if all of these hold:
  1. timestamps strictly increase
  2. sample count matches rate x duration (within --count-tol)
  3. the largest gap between samples is below --gap-factor / rate
  4. if a tick column exists, ticks advance by exactly one per row
  5. no empty or non-numeric cell in the checked columns

Exit status is 0 for PASS and 1 for VOID, so scripts can stop on a bad log.
Standard library only, so it runs on a fresh Raspberry Pi OS image.

Example:
  python3 tools/check_log.py logs/2026-11-03-step.csv --rate 50
  python3 tools/check_log.py logs/run.csv --rate 100 --time-col t --time-unit s
"""

import argparse
import csv
import json
import math
from pathlib import Path
import sys

UNIT_TO_SECONDS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}


def parse_args():
    p = argparse.ArgumentParser(description="Integrity check for a sampled CSV log.")
    p.add_argument("log", help="CSV file with a header row")
    p.add_argument("--rate", type=float, required=True, help="intended sample rate in Hz")
    p.add_argument("--time-col", default="t", help="timestamp column (default: t)")
    p.add_argument("--time-unit", default="s", choices=sorted(UNIT_TO_SECONDS), help="timestamp unit (default: s)")
    p.add_argument("--tick-col", default="tick", help="loop counter column, checked if present (default: tick)")
    p.add_argument("--count-tol", type=float, default=0.01,
                   help="allowed relative difference between actual and expected sample count (default: 0.01)")
    p.add_argument("--gap-factor", type=float, default=1.5,
                   help="largest allowed gap, in sample periods (default: 1.5)")
    p.add_argument("--imu", action="store_true",
                   help="also require a complete collect_imu run record and valid sensor data")
    return p.parse_args()


def check_imu(path, rows, rate):
    """Validate acquisition completion as well as the CSV's timing subset."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.sensing.imu_calib import RAW_FIELDS, UNIT_FIELDS
    from src.sensing.mpu6050 import REGISTERS, SAMPLE_RATE_HZ

    problems = []
    try:
        with open(str(path) + ".json") as stream:
            record = json.load(stream)
        if not isinstance(record, dict):
            raise ValueError("run record must be an object")
        seconds = float(record["seconds"])
        if not math.isfinite(seconds) or seconds < 2 / rate:
            raise ValueError("invalid run duration")
        target = math.ceil(seconds * rate)
        checks = {
            "schema": "mpu6050-sensor-v1", "status": "complete", "reason": "",
            "rate_hz": rate, "sensor_rate_hz": 100.0, "i2c_err": 0,
            "target_samples": target, "samples": len(rows),
            "axis_frame": "sensor_uncalibrated", "motor_interface": "none",
        }
        if rate != SAMPLE_RATE_HZ or len(rows) != target:
            problems.append("IMU rate or full-run sample count mismatch")
        for name, expected in checks.items():
            if record.get(name) != expected:
                problems.append(f"run record {name} mismatch")
        expected_regs = {name: value for name, (_, value) in REGISTERS.items()}
        expected_regs["WHO_AM_I"] = 0x68
        for phase in ("registers_start", "registers_end"):
            actual = record.get(phase, {})
            if not isinstance(actual, dict):
                raise ValueError(f"invalid {phase}")
            for name, expected in expected_regs.items():
                if actual.get(name) != expected:
                    problems.append(f"{phase} {name} mismatch")
        vbat = record["vbat"]
        if vbat is not None and (not math.isfinite(vbat) or vbat <= 0):
            raise ValueError("invalid manual battery voltage")
        if record.get("vbat_source") != ("manual_pre_run" if vbat else "unmeasured"):
            problems.append("battery measurement source mismatch")
        for index, row in enumerate(rows):
            if (int(row["tick"]) != index or row["valid"] != "1" or
                    row["error"] != "" or int(row["i2c_err"]) != 0):
                raise ValueError(f"invalid sample or error at tick {index}")
            for name in RAW_FIELDS:
                if not -32768 <= int(row[name]) <= 32767:
                    raise ValueError(f"raw value out of range: {name}")
            for name in UNIT_FIELDS:
                if not math.isfinite(float(row[name])):
                    raise ValueError(f"nonfinite sensor value: {name}")
            read_s = float(row["read_s"])
            t = float(row["t"])
            if not math.isfinite(read_s) or not 0 <= read_s < 1.5 / rate:
                raise ValueError("invalid read duration")
            if not math.isfinite(t) or not 0 <= t < seconds:
                raise ValueError("sample outside requested time window")
            if index == 0 and t >= 1.5 / rate:
                raise ValueError("first sample arrived late")
            for name in ("theta_deg", "gyro_dps", "wheel_l", "wheel_r", "u_l", "u_r"):
                if row[name] != "":
                    raise ValueError(f"unmeasured body/motor field must be empty: {name}")
            if (vbat is None and row["vbat"] != "") or (
                    vbat is not None and float(row["vbat"]) != vbat):
                raise ValueError("row battery value differs from pre-run measurement")
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        problems.append(f"IMU validation: {exc}")
    return problems


def main():
    args = parse_args()
    if (not math.isfinite(args.rate) or args.rate <= 0 or
            not math.isfinite(args.gap_factor) or args.gap_factor <= 0 or
            not math.isfinite(args.count_tol) or not 0 <= args.count_tol < 1):
        print("VOID: rate/gap-factor must be positive; count-tol must be in [0, 1)")
        return 1
    if args.imu and (args.time_col != "t" or args.tick_col != "tick" or args.time_unit != "s"):
        print("VOID: --imu requires t in seconds and the tick column")
        return 1
    scale = UNIT_TO_SECONDS[args.time_unit]
    problems = []

    with open(args.log, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or args.time_col not in reader.fieldnames:
            print(f"VOID  {args.log}: no '{args.time_col}' column (columns: {reader.fieldnames})")
            return 1
        has_tick = args.tick_col in reader.fieldnames
        times, ticks, rows = [], [], []
        for line_no, row in enumerate(reader, start=2):
            if args.imu:
                rows.append(row)
            try:
                t = float(row[args.time_col]) * scale
                if not math.isfinite(t):
                    raise ValueError
            except (TypeError, ValueError):
                problems.append(f"line {line_no}: bad timestamp {row[args.time_col]!r}")
                continue
            times.append(t)
            if has_tick:
                try:
                    ticks.append(int(row[args.tick_col]))
                except (TypeError, ValueError):
                    problems.append(f"line {line_no}: bad tick {row[args.tick_col]!r}")

    n = len(times)
    if n < 2:
        print(f"VOID  {args.log}: only {n} usable samples")
        return 1

    gaps = [b - a for a, b in zip(times, times[1:])]
    backwards = [i for i, g in enumerate(gaps) if g <= 0]
    if backwards:
        problems.append(f"timestamps do not increase at {len(backwards)} place(s), first after row {backwards[0] + 1}")

    duration = times[-1] - times[0]
    expected = duration * args.rate + 1
    if expected <= 0:
        print(f"VOID  {args.log}: invalid time span")
        return 1
    count_err = (n - expected) / expected
    if abs(count_err) > args.count_tol:
        problems.append(f"sample count {n} vs expected {expected:.0f} ({count_err:+.1%}, limit +/-{args.count_tol:.0%})")

    max_gap = max(gaps)
    if max_gap >= args.gap_factor / args.rate:
        worst = gaps.index(max_gap)
        problems.append(f"largest gap {max_gap * 1e3:.1f} ms after row {worst + 1} "
                        f"(limit {args.gap_factor / args.rate * 1e3:.1f} ms)")

    if has_tick and len(ticks) == n:
        jumps = [i for i, (a, b) in enumerate(zip(ticks, ticks[1:])) if b - a != 1]
        if jumps:
            problems.append(f"tick does not advance by 1 at {len(jumps)} place(s), first after row {jumps[0] + 1}")

    if args.imu:
        problems.extend(check_imu(args.log, rows, args.rate))

    positive = [g for g in gaps if g > 0]
    mean_rate = (len(positive) / sum(positive)) if positive else float("nan")
    summary = (f"n={n} duration={duration:.3f}s expected={expected:.0f} "
               f"mean_rate={mean_rate:.2f}Hz max_gap={max_gap * 1e3:.1f}ms tick={'yes' if has_tick else 'no'}")

    if problems:
        print(f"VOID  {args.log}: {summary}")
        for msg in problems:
            print(f"      - {msg}")
        return 1
    print(f"PASS  {args.log}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
