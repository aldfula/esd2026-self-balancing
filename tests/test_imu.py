"""PC-only acquisition tests: fake I2C, virtual timing, real CSV writer/checker."""

from contextlib import redirect_stdout
from contextlib import nullcontext
from copy import deepcopy
import csv
from decimal import Decimal
import errno
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.sensing.imu_calib import (convert_sample, apply_gyro_bias, load_gyro_bias,
                                   validate_gyro_bias, CORRECTED_FIELDS, RAW_FIELDS,
                                   UNIT_FIELDS)
from src.sensing.mpu6050 import MPU6050
from tools.collect_imu import CSVWriter, collect
from tools import check_log

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.now += delay
        time.sleep(0)  # Let the actual CSV worker run; no simulated wall-time wait.


class Bus:
    def __init__(self, clock, fault=None):
        self.clock = clock
        self.fault = fault
        self.samples = 0
        self.config_writes = []
        self.regs = {0x75: 0x68}

    def write_byte_data(self, address, register, value):
        self.config_writes.append((address, register, value))
        self.regs[register] = value

    def read_byte_data(self, address, register):
        if register == 0x3A:
            return 0 if self.fault == "stale" and self.samples == 3 else 1
        if register == 0x1C and self.fault == "settings":
            return 8
        return self.regs.get(register, 0)

    def read_i2c_block_data(self, address, register, length):
        if register != 0x3B or length != 14:
            raise AssertionError("not a coherent 14-byte sensor burst")
        self.clock.now += 0.001
        if self.samples == 3:
            if self.fault == "i2c":
                self.samples += 1
                raise OSError("injected read failure")
            if self.fault == "short":
                return [0] * 13
            if self.fault == "late":
                self.clock.now += 0.06
        value = self.samples
        self.samples += 1
        return list(struct.pack(">7h", value, -value, 16384, 340, 131, -262, 0))


class IMUTests(unittest.TestCase):
    def acquire(self, fault=None, seconds=30.0, vbat=None, *, writer_diagnostics=False,
                gyro_bias=None):
        clock = Clock()
        bus = Bus(clock, fault)
        stream = io.StringIO(newline="")
        result = collect(MPU6050(bus), stream, seconds, vbat,
                         clock=clock, sleep=clock.sleep,
                         writer_diagnostics=writer_diagnostics, gyro_bias=gyro_bias)
        rows = list(csv.DictReader(io.StringIO(stream.getvalue())))
        return result, rows, stream.getvalue(), bus

    def checker(self, text, result, *, imu=True):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.csv"
            path.write_text(text)
            Path(str(path) + ".json").write_text(json.dumps(result))
            args = ["check_log.py", str(path), "--rate", "30"]
            if imu:
                args.append("--imu")
            output = io.StringIO()
            with patch.object(sys, "argv", args), redirect_stdout(output):
                status = check_log.main()
            return status, output.getvalue()

    def encode_rows(self, rows):
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()

    def test_checker_positive_controls_reject_known_faults(self):
        result, rows, text, _ = self.acquire()
        self.assertEqual(self.checker(text, result)[0], 0)
        # Each mutation starts from the same known-valid captured run.
        mutations = {
            "missing_sample": lambda r: r.pop(400),
            "duplicate_time": lambda r: r[400].update(t=r[399]["t"]),
            "gap": lambda r: r[400].update(t=str(float(r[399]["t"]) + 0.06)),
            "bad_tick": lambda r: r[400].update(tick="999"),
            "invalid_sensor": lambda r: r[400].update(ax_g="nan"),
            "missing_sensor": lambda r: r[400].update(gx_dps=""),
            "wrong_conversion": lambda r: r[400].update(gx_dps="2"),
            "i2c_error": lambda r: r[400].update(i2c_err="1"),
            "invalid_row": lambda r: r[400].update(valid="0"),
            "fabricated_angle": lambda r: r[400].update(theta_deg="0"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                altered = [dict(row) for row in rows]
                mutate(altered)
                status, output = self.checker(self.encode_rows(altered), result)
                self.assertEqual(status, 1, output)
                self.assertIn("VOID", output)
        for name, value in (("status", "running"), ("seconds", 60),
                            ("registers_end", {}), ("rate_hz", 100)):
            with self.subTest(record=name):
                altered = dict(result, **{name: value})
                self.assertEqual(self.checker(text, altered)[0], 1)

    def test_conversion_full_signed_16bit_range_all_seven_channels(self):
        for value in range(-32768, 32768):
            sample = convert_sample(struct.pack(">7h", *([value] * 7)))
            for axis in "xyz":
                self.assertEqual(sample[f"a{axis}_raw"], value)
                self.assertEqual(sample[f"g{axis}_raw"], value)
                self.assertEqual(sample[f"a{axis}_g"], value / 16384)
                self.assertEqual(sample[f"g{axis}_dps"], value / 131)
            self.assertEqual(sample["temp_raw"], value)
            self.assertEqual(sample["temp_c"], value / 340 + 36.53)

    def test_default_run_executes_sensor_path_and_logs_applied_settings(self):
        result, rows, text, bus = self.acquire()
        self.assertEqual(result["status"], "complete", result)
        self.assertNotIn("writer_diagnostics", result)
        self.assertEqual((bus.samples, len(rows), result["samples"]), (900, 900, 900))
        self.assertEqual((rows[0]["tick"], rows[-1]["tick"]), ("0", "899"))
        self.assertNotEqual(rows[0]["ax_raw"], rows[-1]["ax_raw"])
        # Independent expected readback: eleven fields in each of two snapshots.
        expected = {"PWR_MGMT_1": 1, "PWR_MGMT_2": 0, "SMPLRT_DIV": 9,
                    "CONFIG": 5, "GYRO_CONFIG": 0, "ACCEL_CONFIG": 0,
                    "FIFO_EN": 0, "USER_CTRL": 0, "INT_PIN_CFG": 0,
                    "INT_ENABLE": 1, "WHO_AM_I": 0x68}
        for phase in ("registers_start", "registers_end"):
            for field, value in expected.items():
                self.assertEqual(result[phase][field], value)
        self.assertIn((0x68, 0x6B, 0x80), bus.config_writes)
        self.assertEqual(self.checker(text, result)[0], 0)
        self.assertEqual(self.checker(text, result, imu=False)[0], 0)
        self.assertEqual(rows[0]["az_g"], "1.0")
        self.assertEqual(rows[0]["gx_dps"], "1.0")
        self.assertEqual(rows[0]["gy_dps"], "-2.0")
        for field in ("theta_deg", "gyro_dps", "wheel_l", "wheel_r", "u_l", "u_r", "vbat"):
            self.assertEqual(rows[0][field], "")

    def test_first_i2c_fault_stops_and_does_not_reuse_previous_values(self):
        result, rows, text, bus = self.acquire("i2c")
        self.assertEqual(result["status"], "void")
        self.assertEqual((bus.samples, len(rows), result["i2c_err"]), (4, 4, 1))
        self.assertEqual(rows[-1]["valid"], "0")
        self.assertEqual(rows[-1]["ax_raw"], "")
        self.assertEqual(rows[-1]["gx_dps"], "")
        self.assertIn("injected read failure", rows[-1]["error"])
        self.assertEqual(self.checker(text, result)[0], 1)

    def test_stale_short_and_late_samples_stop_at_first_fault(self):
        for fault in ("stale", "short", "late"):
            with self.subTest(fault=fault):
                result, rows, text, _ = self.acquire(fault)
                self.assertEqual(result["status"], "void", result)
                self.assertEqual(len(rows), 4)
                self.assertEqual(rows[-1]["valid"], "0")
                self.assertEqual(self.checker(text, result)[0], 1)

    def test_wrong_identity_or_register_readback_prevents_sampling(self):
        result, rows, _, bus = self.acquire("settings")
        self.assertEqual(result["status"], "void")
        self.assertEqual((len(rows), bus.samples), (0, 0))
        clock = Clock()
        bus = Bus(clock)
        bus.regs[0x75] = 0x70
        with self.assertRaisesRegex(ValueError, "WHO_AM_I"):
            MPU6050(bus).configure(sleep=clock.sleep)
        self.assertEqual(bus.config_writes, [])

    def test_manual_voltage_has_explicit_source_and_consistent_rows(self):
        result, rows, text, _ = self.acquire(seconds=1, vbat=7.8)
        self.assertEqual(result["vbat_source"], "manual_pre_run")
        self.assertTrue(all(row["vbat"] == "7.8" for row in rows))
        self.assertEqual(self.checker(text, result)[0], 0)

    def test_writer_failure_and_interrupt_mark_run_void(self):
        class BrokenStream(io.StringIO):
            def write(self, value):
                raise OSError("injected disk failure")
        clock = Clock()
        result = collect(MPU6050(Bus(clock)), BrokenStream(), 1,
                         clock=clock, sleep=clock.sleep)
        self.assertEqual(result["status"], "void")
        self.assertIn("CSV writer failed", result["reason"])
        sensor = MPU6050(Bus(clock))
        with patch.object(sensor, "read", side_effect=KeyboardInterrupt):
            result = collect(sensor, io.StringIO(), 1, clock=clock, sleep=clock.sleep)
        self.assertEqual(result["status"], "void")
        self.assertIn("interrupted", result["reason"])

    def test_writer_diagnostics_preserve_full_sensor_log_and_bound_history(self):
        result, rows, text, bus = self.acquire(writer_diagnostics=True)
        self.assertEqual((bus.samples, len(rows)), (900, 900))
        self.assertEqual(self.checker(text, result)[0], 0)
        report = result["writer_diagnostics"]
        self.assertEqual(report["queue_capacity"], 128)
        self.assertEqual(len(report["recent_enqueues"]), 128)
        self.assertEqual([entry["tick"] for entry in report["recent_enqueues"]],
                         list(range(772, 900)))
        self.assertIsNone(report["first_fault"])
        self.assertIsNone(report["writer_error"])
        self.assertEqual(report["final"]["rows_completed"], 900)
        self.assertEqual(report["final"]["worker"]["operation"], "done")
        self.assertEqual(report["final"]["queue_size_approx"], 0)
        for name, count in (("header", 1), ("row", 900), ("flush", 1)):
            timing = report["operation_timings"][name]
            self.assertEqual(timing["count"], count)
            self.assertGreaterEqual(timing["max_wall_s"], timing["last_wall_s"])
            self.assertGreaterEqual(timing["max_thread_cpu_s"], 0)
        # The diagnostics must survive the same JSON serialization as a real run.
        self.assertEqual(json.loads(json.dumps(result))["writer_diagnostics"], report)

    def test_diagnostics_distinguish_blocked_write_from_worker_not_started(self):
        class GatedStream(io.StringIO):
            def __init__(self):
                super().__init__()
                self.calls = 0
                self.entered = threading.Event()
                self.release = threading.Event()

            def write(self, value):
                self.calls += 1
                if self.calls == 2:  # Header succeeds; block the first data write.
                    self.entered.set()
                    if not self.release.wait(2):
                        raise TimeoutError("test did not release blocked stream")
                return super().write(value)

        stream = GatedStream()
        writer = CSVWriter(stream, diagnostics=True)
        try:
            writer.put({"tick": 0})
            self.assertTrue(stream.entered.wait(1))
            for tick in range(1, 129):
                writer.put({"tick": tick})
            with self.assertRaisesRegex(OSError, "CSV queue full"):
                writer.put({"tick": 129})
            blocked = writer.first_fault
            self.assertEqual(blocked["queue_size_approx"], 128)
            self.assertEqual(blocked["tick"], 129)
            self.assertEqual(blocked["rows_completed"], 0)
            self.assertEqual(blocked["worker"]["operation"], "row")
            self.assertEqual(blocked["worker"]["tick"], 0)
        finally:
            stream.release.set()
            writer.close()
        report = writer.report()
        self.assertEqual(report["first_fault"], blocked)
        self.assertEqual(report["final"]["queue_size_approx"], 0)
        self.assertEqual(report["final"]["rows_completed"], 129)
        self.assertEqual(report["queue_high_water_observed"], 128)
        self.assertEqual(report["operation_timings"]["row"]["max_wall_tick"], 0)
        self.assertGreaterEqual(report["operation_timings"]["row"]["max_wall_s"],
                                blocked["worker"]["elapsed_s"])

        entered = threading.Event()
        release = threading.Event()

        class GatedWorker(CSVWriter):
            def _write(self, stream):
                entered.set()
                if not release.wait(2):
                    self.error = TimeoutError("test did not release worker")
                    return
                super()._write(stream)

        stream = io.StringIO()
        writer = GatedWorker(stream, diagnostics=True)
        try:
            self.assertTrue(entered.wait(1))
            for tick in range(128):
                writer.put({"tick": tick})
            with self.assertRaisesRegex(OSError, "CSV queue full"):
                writer.put({"tick": 128})
            delayed = writer.first_fault
            self.assertEqual(delayed["queue_size_approx"], 128)
            self.assertEqual(delayed["worker"]["operation"], "starting")
            self.assertEqual(delayed["rows_completed"], 0)
        finally:
            release.set()
            writer.close()
        self.assertEqual(writer.report()["first_fault"], delayed)
        self.assertEqual(writer.rows, 128)

    def test_writer_diagnostics_preserve_disk_full_errno_and_failing_phase(self):
        class FullDisk(io.StringIO):
            def write(self, value):
                if self.tell():
                    raise OSError(errno.ENOSPC, "injected disk full")
                return super().write(value)

        clock = Clock()
        result = collect(MPU6050(Bus(clock)), FullDisk(), 1,
                         clock=clock, sleep=clock.sleep, writer_diagnostics=True)
        self.assertEqual(result["status"], "void")
        self.assertIn("CSV writer failed", result["reason"])
        report = result["writer_diagnostics"]
        self.assertEqual(report["writer_error"]["errno"], errno.ENOSPC)
        self.assertEqual(report["writer_error"]["worker"]["operation"], "row")
        self.assertEqual(report["writer_error"]["worker"]["tick"], 0)
        self.assertEqual(result["samples"], 0)

    def test_checker_rejects_exact_gap_boundary_and_invalid_rates(self):
        # 20 Hz makes the boundary exactly representable as the same expression.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boundary.csv"
            path.write_text(f"t,tick\n0,0\n{1.5 / 20},1\n")
            with patch.object(sys, "argv", ["check_log", str(path), "--rate", "20",
                                           "--count-tol", "0.5"]), redirect_stdout(io.StringIO()):
                self.assertEqual(check_log.main(), 1)
            for rate in ("0", "-30", "nan", "inf"):
                with patch.object(sys, "argv", ["check_log", str(path), "--rate", rate]), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(check_log.main(), 1)

    def test_cli_help_and_missing_dependency_do_not_touch_i2c(self):
        completed = subprocess.run([sys.executable, "-m", "tools.collect_imu", "--help"],
                                   cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--output", completed.stdout)
        # Explicitly make smbus2 unavailable regardless of the host's packages.
        from tools.collect_imu import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unused.csv"
            with patch.dict(sys.modules, {"smbus2": None}), \
                    patch.object(sys, "argv", ["collect_imu", "--output", str(path)]), \
                    redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(main(), 1)
            self.assertFalse(path.exists())

    def test_cli_saves_pair_external_checker_and_preserves_existing_log(self):
        from tools import collect_imu
        clock = Clock()
        bus = Bus(clock)
        fake_smbus = SimpleNamespace(SMBus=lambda number: nullcontext(bus))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.csv"
            arguments = ["collect_imu", "--writer-diagnostics", "--output", str(path),
                         "--seconds", "1"]
            def virtual_collect(*args, **kwargs):
                return collect(*args, clock=clock, sleep=clock.sleep, **kwargs)
            with patch.dict(sys.modules, {"smbus2": fake_smbus}), \
                    patch.object(sys, "argv", arguments), \
                    patch.object(collect_imu, "collect", side_effect=virtual_collect), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(collect_imu.main(), 0)
            checked = subprocess.run(
                [sys.executable, "tools/check_log.py", str(path), "--rate", "30", "--imu"],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("PASS", checked.stdout)
            # A second command requests a different run but must preserve the first.
            with patch.dict(sys.modules, {"smbus2": fake_smbus}), \
                    patch.object(sys, "argv", arguments[:-1] + ["2"]), \
                    patch("sys.stderr", io.StringIO()):
                self.assertEqual(collect_imu.main(), 1)
            record = json.loads(Path(str(path) + ".json").read_text())
            self.assertEqual(record["seconds"], 1)
            self.assertEqual(record["samples"], 30)
            self.assertEqual(record["writer_diagnostics"]["operation_timings"]["row"]["count"], 30)
            with path.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 30)
            self.assertEqual(rows[-1]["tick"], "29")
            # Verify the corrected strict boundary through a separate CLI process.
            path = Path(directory) / "boundary.csv"
            path.write_text("t,tick\n0,0\n0.075,1\n")
            checked = subprocess.run(
                [sys.executable, "tools/check_log.py", str(path), "--rate", "20",
                 "--count-tol", "0.5"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
            self.assertIn("largest gap", checked.stdout)

    def test_adopted_config_and_full_range_correction_against_decimal(self):
        config = load_gyro_bias(ROOT / "config/imu_gyro_bias.json")
        expected_bias = {"x": Decimal("-1.487040"), "y": Decimal("1.471874"),
                         "z": Decimal("-2.600636")}
        self.assertEqual(config["bias_dps"], {k: float(v) for k, v in expected_bias.items()})
        self.assertEqual(config["evidence"]["adoption_commit"],
                         "d509491d44742c9c2242348784b1f9282e86ee07")
        for name in ("source_log", "validation_record"):
            self.assertTrue((ROOT / config["evidence"][name]).is_file())
        for value in range(-32768, 32768):
            sample = convert_sample(struct.pack(">7h", *([value] * 7)))
            corrected = apply_gyro_bias(sample, config)
            self.assertEqual(sample, {k: corrected[k] for k in sample})
            self.assertFalse(set(CORRECTED_FIELDS) & sample.keys())
            for axis, field in zip("xyz", CORRECTED_FIELDS):
                expected = Decimal(value) / Decimal(131) - expected_bias[axis]
                self.assertLessEqual(abs(Decimal(str(corrected[field])) - expected),
                                     Decimal("1e-12"))

    def test_bias_zero_residual_and_double_application_guard(self):
        config = load_gyro_bias(ROOT / "config/imu_gyro_bias.json")
        sample = {f"g{a}_dps": config["bias_dps"][a] for a in "xyz"}
        corrected = apply_gyro_bias(sample, config)
        self.assertEqual([corrected[k] for k in CORRECTED_FIELDS], [0, 0, 0])
        with self.assertRaisesRegex(ValueError, "already present"):
            apply_gyro_bias(corrected, config)
        sensor_clock = Clock()
        sensor = MPU6050(Bus(sensor_clock))
        original_read = sensor.read
        with patch.object(sensor, "read", side_effect=lambda: apply_gyro_bias(original_read(), config)):
            result = collect(sensor, io.StringIO(), 1, clock=sensor_clock,
                             sleep=sensor_clock.sleep, gyro_bias=config)
        self.assertEqual(result["status"], "void")
        self.assertEqual(result["samples"], 1)
        self.assertIn("already present", result["reason"])

    def test_invalid_bias_config_rejected_before_sensor_or_output(self):
        config = load_gyro_bias(ROOT / "config/imu_gyro_bias.json")
        malformed = [None, [], {}, dict(config, unknown=1)]
        for key, value in (("units", "rad/s"), ("axis_frame", "body"),
                           ("schema", "unknown"), ("operation", "g + b"),
                           ("evidence", {}), ("bias_dps", {"x": 0, "y": 0})):
            malformed.append(dict(config, **{key: value}))
        for value in (True, "1", None, float("nan"), float("inf"), -251, 10 ** 500):
            bad = deepcopy(config)
            bad["bias_dps"]["x"] = value
            malformed.append(bad)
        malformed.append(dict(config, evidence=dict(config["evidence"], adoption_commit="bad")))
        from tools import collect_imu
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            output = Path(directory) / "unused.csv"
            for bad in malformed:
                with self.subTest(bad=repr(bad)[:100]):
                    with self.assertRaises(ValueError):
                        validate_gyro_bias(bad)
                    path.write_text(json.dumps(bad))
                    with patch.object(sys, "argv", ["collect_imu", "--output", str(output),
                                                    "--gyro-bias", str(path)]), \
                            patch.dict(sys.modules, {"smbus2": SimpleNamespace(
                                SMBus=lambda n: self.fail("invalid config touched I2C"))}), \
                            patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as stopped:
                        collect_imu.main()
                    self.assertEqual(stopped.exception.code, 2)
                    self.assertFalse(output.exists())
                    self.assertFalse(Path(str(output) + ".json").exists())
            for text in ('{"schema": "x", "schema": "y"}', '{invalid'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    load_gyro_bias(path)
            with self.assertRaises(OSError):
                load_gyro_bias(Path(directory) / "missing.json")
        clock = Clock()
        bus = Bus(clock)
        stream = io.StringIO()
        with self.assertRaises(ValueError):
            collect(MPU6050(bus), stream, 1, gyro_bias={})
        self.assertEqual(bus.config_writes, [])
        self.assertEqual(stream.getvalue(), "")

    def test_explicit_bias_run_preserves_baseline_and_records_snapshot(self):
        config = load_gyro_bias(ROOT / "config/imu_gyro_bias.json")
        baseline, raw_rows, _, _ = self.acquire(seconds=1)
        result, rows, text, bus = self.acquire(seconds=1, gyro_bias=config,
                                              writer_diagnostics=True)
        self.assertEqual((bus.samples, result["samples"], len(rows)), (30, 30, 30))
        self.assertEqual(result["schema"], "mpu6050-sensor-v2")
        self.assertIs(result["gyro_bias"]["applied"], True)
        self.assertEqual(result["gyro_bias"]["config"], config)
        config["bias_dps"]["x"] = 0
        self.assertEqual(result["gyro_bias"]["config"]["bias_dps"]["x"], -1.487040)
        for before, after in zip(raw_rows, rows):
            self.assertEqual(before, {key: after[key] for key in before})
        self.assertEqual(self.checker(text, result)[0], 0)
        self.assertEqual(baseline["schema"], "mpu6050-sensor-v1")
        failed, rows, text, _ = self.acquire("i2c", gyro_bias=load_gyro_bias(
            ROOT / "config/imu_gyro_bias.json"))
        self.assertEqual((failed["status"], len(rows)), ("void", 4))
        self.assertTrue(all(rows[-1][name] == "" for name in CORRECTED_FIELDS))
        self.assertEqual(self.checker(text, failed)[0], 1)

    def test_corrected_checker_positive_controls(self):
        config = load_gyro_bias(ROOT / "config/imu_gyro_bias.json")
        result, rows, text, _ = self.acquire(seconds=1, gyro_bias=config)
        self.assertEqual(self.checker(text, result)[0], 0)
        for name, value in (("gx_corrected_dps", "nan"), ("gy_corrected_dps", ""),
                            ("gz_corrected_dps", "0"), ("gx_dps", "2")):
            changed = deepcopy(rows)
            changed[0][name] = value
            with self.subTest(field=name):
                self.assertEqual(self.checker(self.encode_rows(changed), result)[0], 1)
        for sign in (-1, 1):
            changed = deepcopy(rows)
            # Inject either an addition in place of subtraction, or subtraction twice.
            changed[0]["gx_corrected_dps"] = str(float(rows[0]["gx_dps"]) +
                sign * (2 if sign == -1 else 1) * config["bias_dps"]["x"])
            self.assertEqual(self.checker(self.encode_rows(changed), result)[0], 1)
        changed = [{k: v for k, v in row.items() if k != CORRECTED_FIELDS[0]} for row in rows]
        self.assertEqual(self.checker(self.encode_rows(changed), result)[0], 1)
        for update in ({"schema": "mpu6050-sensor-v1"}, {"schema": "unknown"},
                       {"gyro_bias": {"applied": False, "config": config}},
                       {"gyro_bias": {"applied": 1, "config": config}},
                       {"gyro_bias": None}):
            self.assertEqual(self.checker(text, dict(result, **update))[0], 1)
        bad_record = deepcopy(result)
        bad_record["gyro_bias"]["config"]["bias_dps"]["x"] = 0
        self.assertEqual(self.checker(text, bad_record)[0], 1)
        no_columns = [{k: v for k, v in row.items() if k not in CORRECTED_FIELDS} for row in rows]
        self.assertEqual(self.checker(self.encode_rows(no_columns), result)[0], 1)
        legacy, legacy_rows, _, _ = self.acquire(seconds=1)
        legacy_rows[0]["gx_corrected_dps"] = ""
        for row in legacy_rows[1:]:
            row["gx_corrected_dps"] = ""
        self.assertEqual(self.checker(self.encode_rows(legacy_rows), legacy)[0], 1)

    def test_bias_cli_pair_and_external_checker_with_fake_i2c(self):
        from tools import collect_imu
        config_path = ROOT / "config/imu_gyro_bias.json"
        clock = Clock()
        bus = Bus(clock)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrected.csv"
            def virtual_collect(*args, **kwargs):
                return collect(*args, clock=clock, sleep=clock.sleep, **kwargs)
            with patch.dict(sys.modules, {"smbus2": SimpleNamespace(SMBus=lambda n: nullcontext(bus))}), \
                    patch.object(sys, "argv", ["collect_imu", "--output", str(path),
                                              "--seconds", "1", "--gyro-bias", str(config_path)]), \
                    patch.object(collect_imu, "collect", side_effect=virtual_collect), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(collect_imu.main(), 0)
            record = json.loads(Path(str(path) + ".json").read_text())
            self.assertEqual(record["gyro_bias_config_path"], str(config_path))
            self.assertEqual(record["gyro_bias"]["config"], load_gyro_bias(config_path))
            checked = subprocess.run([sys.executable, "-B", "tools/check_log.py", str(path),
                                      "--rate", "30", "--imu"], cwd=ROOT,
                                     capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_three_existing_runs_replay_against_independent_decimal(self):
        config = load_gyro_bias(ROOT / "config/imu_gyro_bias.json")
        biases = [Decimal("-1.487040"), Decimal("1.471874"), Decimal("-2.600636")]
        fields_checked = 0
        for run in ("r03", "r04", "r05"):
            path = ROOT / f"logs/imu-bias-validation-2026-11-24-{run}.csv"
            original = path.read_text()
            record = json.loads(Path(str(path) + ".json").read_text())
            self.assertEqual(self.checker(original, record)[0], 0)
            source = list(csv.DictReader(io.StringIO(original)))
            clock = Clock()
            class ReplayBus(Bus):
                def read_i2c_block_data(self, address, register, length):
                    self.clock.now += 0.001
                    raw = [int(source[self.samples][name]) for name in RAW_FIELDS]
                    self.samples += 1
                    return list(struct.pack(">7h", *raw))
            bus = ReplayBus(clock)
            stream = io.StringIO()
            result = collect(MPU6050(bus), stream, 30, record["vbat"], clock=clock,
                             sleep=clock.sleep, gyro_bias=config)
            self.assertEqual((result["status"], bus.samples), ("complete", 900))
            self.assertEqual(self.checker(stream.getvalue(), result)[0], 0)
            replay = list(csv.DictReader(io.StringIO(stream.getvalue())))
            self.assertEqual(len(replay), len(source))
            for before, after in zip(source, replay):
                for name in RAW_FIELDS + UNIT_FIELDS:
                    self.assertEqual(before[name], after[name])
                    fields_checked += 1
                for axis, bias, field in zip("xyz", biases, CORRECTED_FIELDS):
                    expected = Decimal(before[f"g{axis}_raw"]) / Decimal(131) - bias
                    self.assertLessEqual(abs(Decimal(after[field]) - expected), Decimal("1e-12"))
                    fields_checked += 1
            with path.open() as source_stream:
                self.assertEqual(list(csv.DictReader(source_stream)), source)
        self.assertEqual(fields_checked, 45900)


if __name__ == "__main__":
    unittest.main()
