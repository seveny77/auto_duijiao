# -*- coding: utf-8 -*-
"""自动对焦闭环 v1（全扫）测试：参数换算 + 模拟全流程 + 丢帧报错"""

import json
import os
import sys
import time

import cv2
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from autofocus_controller import (  # noqa: E402
    AutofocusConfig,
    AutofocusController,
    AutofocusError,
    FrameCollector,
    build_parser,
    config_from_args,
)
from autofocus_sim import FakePlcClient, ScoreMapEvaluator, SimCamera  # noqa: E402
from plc.client import PlcClient, PlcTimeoutError  # noqa: E402
from plc.protocol import (  # noqa: E402
    REG_FLYSCAN_CONFIRM,
    REG_FLYSCAN_DONE,
    REG_FLYSCAN_TRIGGER,
    REG_MOVE_CONFIRM,
    REG_MOVE_DONE,
    REG_MOVE_INDEX,
    REG_MOVE_TRIGGER,
)
from camera.camera_adapter import HikCamera  # noqa: E402


SCORES_PATH = os.path.join(ROOT, "_scores.json")


@pytest.fixture(scope="module")
def scores():
    with open(SCORES_PATH, encoding="utf-8-sig") as f:
        return json.load(f)


def make_config(**kw) -> AutofocusConfig:
    base = dict(
        mode="sim",
        sim_scores_path=SCORES_PATH,
        template_path=os.path.join(ROOT, "data/template.json"),
        skip_confirm=True,
    )
    base.update(kw)
    return AutofocusConfig(**base)


def make_controller(config, scores, expected_count=None, n_frames=None):
    n = n_frames or 100
    plc = FakePlcClient(11000, 12000, expected_count or n)
    cam = SimCamera(n=n, interval_s=0.001)
    evaluator = ScoreMapEvaluator(scores)
    return AutofocusController(config, plc=plc, camera=cam, evaluator=evaluator)


# ---------- PLC 定点移动握手 ----------
class _BinningCamStub:
    """桩：模拟 SDK 的 SetIntValue/GetIntValue。"""

    def __init__(self, fail_val=False):
        self.calls = []
        self.fail_val = fail_val
        self.vals = {}

    def MV_CC_SetIntValue(self, name, val):
        self.calls.append(("set", name, val))
        if self.fail_val and name.endswith("_Val"):
            return 0x80000109
        self.vals[name] = val
        return 0

    def MV_CC_GetIntValue(self, name, st):
        st.nCurValue = self.vals.get(name, 0)
        return 0


class TestSetBinning:
    def test_prefers_val_nodes(self):
        cam = HikCamera(0)
        cam._cam = _BinningCamStub()
        cam.set_binning(2, 2)
        sets = [c for c in cam._cam.calls if c[0] == "set"]
        assert ("set", "BinningHorizontal_Val", 2) in sets
        assert ("set", "BinningVertical_Val", 2) in sets
        assert not any(
            n in ("BinningHorizontal", "BinningVertical") for _, n, _ in sets
        )

    def test_falls_back_to_standard_nodes(self):
        cam = HikCamera(0)
        cam._cam = _BinningCamStub(fail_val=True)
        cam.set_binning(2, 2)
        sets = [c for c in cam._cam.calls if c[0] == "set"]
        assert ("set", "BinningHorizontal", 2) in sets
        assert ("set", "BinningVertical", 2) in sets


class TestSetDecimation:
    def test_prefers_val_nodes(self):
        cam = HikCamera(0)
        cam._cam = _BinningCamStub()
        cam.set_decimation(2, 2)
        sets = [c for c in cam._cam.calls if c[0] == "set"]
        assert ("set", "DecimationHorizontal_Val", 2) in sets
        assert ("set", "DecimationVertical_Val", 2) in sets
        assert not any(
            n in ("DecimationHorizontal", "DecimationVertical") for _, n, _ in sets
        )

    def test_falls_back_to_standard_nodes(self):
        cam = HikCamera(0)
        cam._cam = _BinningCamStub(fail_val=True)
        cam.set_decimation(2, 2)
        sets = [c for c in cam._cam.calls if c[0] == "set"]
        assert ("set", "DecimationHorizontal", 2) in sets
        assert ("set", "DecimationVertical", 2) in sets


class TestFrameCollectorEvalStats:
    def test_eval_stats_records_per_frame_time(self):
        class SlowEval:
            def __init__(self, delay=0.01):
                self.delay = delay

            def evaluate_image(self, img, roi=None):
                time.sleep(self.delay)
                return 1.0

        cam = SimCamera(n=5, interval_s=0.001)
        collector = FrameCollector(cam, SlowEval(0.01), None)
        collector.start()
        try:
            assert collector.wait(5, timeout=5.0)
            stats = collector.eval_stats()
        finally:
            collector.stop()

        assert stats["count"] == 5
        assert 8.0 < stats["avg_ms"] < 20.0
        assert stats["min_ms"] > 5.0
        assert stats["max_ms"] < 25.0
        assert collector.last_image is not None

    def test_save_image_writes_png(self, tmp_path):
        img = (np.random.rand(64, 64, 3) * 255).astype(np.uint8)
        path = str(tmp_path / "final.png")
        AutofocusController._save_image(img, path)
        arr = np.fromfile(path, dtype=np.uint8)
        dec = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        assert dec is not None
        assert dec.shape == (64, 64, 3)


class _RegResult:
    def __init__(self, registers):
        self.registers = registers

    def isError(self):
        return False


class _RegBank:
    """桩：模拟 pymodbus 寄存器读写，记录写入顺序。"""

    def __init__(self):
        self.regs = {}
        self.writes = []

    def read_holding_registers(self, addr, count=1):
        return _RegResult([self.regs.get(addr + i, 0) for i in range(count)])

    def write_register(self, addr, value):
        self.regs[addr] = int(value)
        self.writes.append((addr, int(value)))
        return _RegResult([])

    def write_registers(self, addr, values):
        vals = [int(v) for v in values]
        for i, v in enumerate(vals):
            self.regs[addr + i] = v
        self.writes.append((addr, vals))
        return _RegResult([])

    def close(self):
        pass

    @property
    def connected(self):
        return True


class TestPlcMoveHandshake:
    def _make_client(self):
        c = PlcClient("192.168.100.88", 502)
        c._client = _RegBank()
        return c

    def test_trigger_edge_order(self):
        c = self._make_client()
        bank = c._client
        # 确认/完成直接视为置位，跳过轮询
        c._wait_register = lambda addr, expected, timeout, poll_interval=0.05: True
        c.move_to_position(5)

        first_trig = next(
            i for i, (a, v) in enumerate(bank.writes) if a == REG_MOVE_TRIGGER
        )
        i_trigger_1 = next(
            i
            for i, (a, v) in enumerate(bank.writes)
            if a == REG_MOVE_TRIGGER and v == 1
        )
        i_index = next(
            i for i, (a, v) in enumerate(bank.writes) if a == REG_MOVE_INDEX
        )

        assert bank.writes[first_trig][1] == 0      # 触发位先归零
        assert first_trig < i_index < i_trigger_1   # 顺序：0 → index → 1
        assert bank.regs.get(REG_MOVE_INDEX) == 5

    def test_confirm_timeout_error_has_snapshot(self):
        c = self._make_client()
        # 确认永不置位，轮询立即返回 False
        c._wait_register = lambda addr, expected, timeout, poll_interval=0.05: False
        with pytest.raises(PlcTimeoutError) as exc:
            c.move_to_position(5)
        msg = str(exc.value)
        assert "定点移动确认超时" in msg
        assert "D4011" in msg
        assert "D4112" in msg


# ---------- 参数换算 ----------
class TestConfigFromArgs:
    def test_defaults_from_dataclass(self):
        args = build_parser().parse_args([])
        cfg = config_from_args(args)
        assert cfg.roi == (840, 500, 700, 700)
        assert cfg.binning == 2
        assert cfg.stroke_min_um == 11000
        assert cfg.stroke_max_um == 12000
        assert cfg.exposure_us == 400

    def test_explicit_args_override(self):
        args = build_parser().parse_args(
            [
                "--roi", "100,200,50,50",
                "--binning", "2",
                "--stroke-min-um", "5000",
                "--stroke-max-um", "15000",
                "--exposure-us", "300",
                "--hardware-roi",
            ]
        )
        cfg = config_from_args(args)
        assert cfg.roi == (100, 200, 50, 50)
        assert cfg.binning == 2
        assert cfg.stroke_min_um == 5000
        assert cfg.stroke_max_um == 15000
        assert cfg.exposure_us == 300
        assert cfg.hardware_roi is True


class TestFlyscanParams:
    def test_align_window_keeps_aligned(self):
        assert AutofocusController._align_window((840, 500, 700, 700)) == (840, 500, 700, 700)

    def test_align_window_aligns_down(self):
        assert AutofocusController._align_window((406, 405, 900, 900)) == (404, 404, 900, 900)

    def test_align_window_out_of_bounds(self):
        with pytest.raises(AutofocusError):
            AutofocusController._align_window((2000, 2000, 900, 900))

    def test_uniform_stroke(self):
        # PLC 缺首：跨度 1000、N=100 → 步距 10，飞拍起点=行程min 11000，终点 12000
        assert AutofocusController.compute_flyscan_params(11000, 12000, 100) == (11000, 12000, 10)

    def test_fractional_um_per_index(self):
        # 1000/100=10 整，飞拍起点 0，终点恰好 1000
        assert AutofocusController.compute_flyscan_params(0, 1000, 100) == (0, 1000, 10)
        # 非整除：round(1000/101)=10 → 0+1010>1000 → 回退 1000//101=9 → 终点 909
        assert AutofocusController.compute_flyscan_params(0, 1000, 101) == (0, 909, 9)

    def test_step_driven(self):
        # 步距 10 → N = 1000//10 = 100，飞拍起点 0，终点正好 1000
        assert AutofocusController.compute_flyscan_params(0, 1000, step_um=10) == (0, 1000, 10)
        # 真机行程 5000~15000、步距 50 → N = 200
        assert AutofocusController.compute_flyscan_params(5000, 15000, step_um=50) == (5000, 15000, 50)
        # 非整除向下取整：1000//30 = 33 → 终点 990
        assert AutofocusController.compute_flyscan_params(0, 1000, step_um=30) == (0, 990, 30)

    def test_floor_fallback(self):
        # N 驱动非整除回退：round(1000/150)=7 超行程 → 1000//150=6 → 飞拍起点 0、终点 900
        assert AutofocusController.compute_flyscan_params(0, 1000, 150) == (0, 900, 6)

    def test_step_too_large(self):
        # 步距 ≥ 行程 → 张数 < 2，报错
        with pytest.raises(AutofocusError):
            AutofocusController.compute_flyscan_params(0, 1000, step_um=1000)

    def test_invalid_stroke(self):
        with pytest.raises(AutofocusError):
            AutofocusController.compute_flyscan_params(12000, 11000, 100)

    def test_step_too_small(self):
        with pytest.raises(AutofocusError):
            AutofocusController.compute_flyscan_params(0, 1, 100)


# ---------- 模拟全流程 ----------
class TestSimFullscan:
    def test_run_reports_peak_and_moves(self, scores):
        cfg = make_config()
        controller = make_controller(cfg, scores)
        report = controller.run()

        assert report["best_index"] == 68
        assert report["move_index"] == 69          # PLC 定点表 1 起始：帧序 68 → PLC index 69
        assert report["plc_count"] == 100
        assert report["processed"] == 100
        assert report["eval_stats"]["count"] == 100
        assert report["move_s"] == pytest.approx(0.01, abs=0.02)   # FakePlc 固定延时
        assert report["ct_s"] == pytest.approx(
            report["flyscan_s"] + report["evaluate_wait_s"] + report["move_s"],
            abs=0.1,
        )
        assert report["final_image_saved"] is False   # sim 模式不保存定点图
        assert report["hardware_roi"] is False
        # 默认行程 11000~12000、N=100 → 步距 10，飞拍起点 11000，终点 12000
        assert report["flyscan"] == (11000, 12000, 10)

        plc = controller._plc
        assert plc.last_move_index == 69
        assert plc.completed is True

    def test_config_overrides_stroke(self, scores):
        cfg = make_config(stroke_min_um=0, stroke_max_um=990)
        controller = make_controller(cfg, scores)
        report = controller.run()
        # 990/100 round=10 超行程 → 回退 9 → 飞拍起点 0、终点 900
        assert report["flyscan"] == (0, 900, 9)

    def test_hardware_roi_flag_reported(self, scores):
        cfg = make_config(hardware_roi=True)
        controller = make_controller(cfg, scores)
        report = controller.run()
        assert report["hardware_roi"] is True
        assert report["best_index"] == 68


# ---------- 异常处理 ----------
class TestFailurePaths:
    def test_step_um_and_num_images_mutually_exclusive(self, scores):
        cfg = make_config(step_um=50, num_images=100)
        controller = make_controller(cfg, scores)
        with pytest.raises(AutofocusError):
            controller.run()

    def test_frame_count_mismatch_raises(self, scores):
        cfg = make_config()
        controller = make_controller(cfg, scores, expected_count=90)
        with pytest.raises(AutofocusError):
            controller.run()

    def test_missing_frame_raises(self, scores):
        # 相机只出 95 帧但 PLC 认为 100 帧 → 缺帧报错
        cfg = make_config(frame_wait_timeout_s=1.0)
        controller = make_controller(cfg, scores, expected_count=100, n_frames=95)
        with pytest.raises(AutofocusError):
            controller.run()
