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
import math
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
    return p.parse_args()


def main():
    args = parse_args()
    scale = UNIT_TO_SECONDS[args.time_unit]
    problems = []

    with open(args.log, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or args.time_col not in reader.fieldnames:
            print(f"VOID  {args.log}: no '{args.time_col}' column (columns: {reader.fieldnames})")
            return 1
        has_tick = args.tick_col in reader.fieldnames
        times, ticks = [], []
        for line_no, row in enumerate(reader, start=2):
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

    period = 1.0 / args.rate
    gaps = [b - a for a, b in zip(times, times[1:])]
    backwards = [i for i, g in enumerate(gaps) if g <= 0]
    if backwards:
        problems.append(f"timestamps do not increase at {len(backwards)} place(s), first after row {backwards[0] + 1}")

    duration = times[-1] - times[0]
    expected = duration * args.rate + 1
    count_err = (n - expected) / expected
    if abs(count_err) > args.count_tol:
        problems.append(f"sample count {n} vs expected {expected:.0f} ({count_err:+.1%}, limit +/-{args.count_tol:.0%})")

    max_gap = max(gaps)
    if max_gap > args.gap_factor * period:
        worst = gaps.index(max_gap)
        problems.append(f"largest gap {max_gap * 1e3:.1f} ms after row {worst + 1} "
                        f"(limit {args.gap_factor * period * 1e3:.1f} ms)")

    if has_tick and len(ticks) == n:
        jumps = [i for i, (a, b) in enumerate(zip(ticks, ticks[1:])) if b - a != 1]
        if jumps:
            problems.append(f"tick does not advance by 1 at {len(jumps)} place(s), first after row {jumps[0] + 1}")

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
