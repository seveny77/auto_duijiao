# -*- coding: utf-8 -*-
"""模拟硬件：不依赖相机SDK/真实轴卡，用于离线跑通闭环。"""

import threading
import time
from typing import List, Optional, Tuple

import numpy as np
from motion.state import MotionState


class FakeMotionBackend:
    """模拟运动后端：实现与LCT后端相同的上层接口。"""

    def __init__(self, stroke_min: int, stroke_max: int):
        self.stroke = (stroke_min, stroke_max)
        self.connected = True
        self._servo_enabled = True
        self._homed = True
        self._position_um = float(stroke_min)
        self.last_flyscan: Optional[Tuple[int, int, int]] = None
        self.last_capture_position: Optional[int] = None

    @property
    def backend_name(self) -> str:
        return "sim"

    def connect(self):
        self.connected = True
        self._servo_enabled = False
        self._homed = False

    def prepare_new_task(self) -> None:
        """仿真后端没有跨任务取消状态，保持接口一致。"""

        if not self.connected:
            raise RuntimeError("仿真运动控制器未连接")

    def disconnect(self):
        self.connected = False
        self._servo_enabled = False
        self._homed = False

    def read_stroke_range(self) -> Tuple[int, int]:
        return self.stroke

    def get_state(self) -> MotionState:
        return MotionState(
            connected=self.connected,
            servo_enabled=self._servo_enabled,
            homed=self._homed,
            position_um=self._position_um,
            stroke_min_um=self.stroke[0],
            stroke_max_um=self.stroke[1],
            ready_for_autofocus=(
                self.connected and self._servo_enabled and self._homed
            ),
            operation="idle" if self.connected else "disconnected",
            message=("仿真已就绪" if self.connected else "未连接"),
        )

    def is_ready_for_autofocus(self) -> bool:
        return self.get_state().ready_for_autofocus

    def clear_alarm(self) -> MotionState:
        return self.get_state()

    def servo_on(self) -> MotionState:
        if not self.connected:
            raise RuntimeError("仿真运动控制器未连接")
        self._servo_enabled = True
        return self.get_state()

    def servo_off(self) -> MotionState:
        self._servo_enabled = False
        return self.get_state()

    def home(self, cancel_event=None, timeout_s=None) -> MotionState:
        if not self.connected:
            raise RuntimeError("仿真运动控制器未连接")
        self._servo_enabled = True
        self._homed = True
        return self.get_state()

    def cancel_current_motion(self) -> None:
        self._servo_enabled = False

    def is_connected(self) -> bool:
        return self.connected

    def linear_fly_scan(
        self,
        start_um: int,
        end_um: int,
        step_um: int,
        timeout_s: float,
        cancel_event=None,
        phase_name: str = "",
        velocity_um_s: float = None,
    ) -> int:
        if step_um <= 0 or end_um <= start_um:
            raise ValueError("模拟飞拍区间或步距无效")
        span_um = end_um - start_um
        if span_um % step_um != 0:
            raise ValueError("模拟飞拍区间必须是步距的整数倍")
        self.last_flyscan = (start_um, end_um, step_um)
        self.last_flyscan_velocity_um_s = velocity_um_s
        self._position_um = float(end_um)
        time.sleep(0.05)
        return span_um // step_um

    def capture_at_position(
        self,
        position_um: int,
        timeout_s: float,
        cancel_event=None,
    ) -> int:
        self.last_capture_position = position_um
        self._position_um = float(position_um)
        time.sleep(0.01)
        return 1

    def move_to_position(self, position_um, timeout_s, cancel_event=None):
        if not self.connected or not self._servo_enabled or not self._homed:
            raise RuntimeError("仿真运动控制器未就绪")
        if not self.stroke[0] <= position_um <= self.stroke[1]:
            raise ValueError("仿真目标位置超出行程")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("用户取消")
        self._position_um = float(position_um)
        return self.get_state()


class FakePlcClient:
    """旧版控制器测试替身。

    现行流水线已经使用 ``FakeMotionBackend``，这里保留这个小兼容层，
    让尚未迁移的旧控制器单元测试仍然可以离线运行。它不参与真实任务。
    """

    def __init__(self, stroke_min: int, stroke_max: int, expected_count: int):
        self.stroke = (stroke_min, stroke_max)
        self.expected_count = expected_count
        self.connected = False
        self.last_flyscan: Optional[Tuple[int, int, int]] = None
        self.last_move_index: Optional[int] = None
        self.completed = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def read_stroke_range(self) -> Tuple[int, int]:
        return self.stroke

    def flyscan_trigger(self, start_pos_um, end_pos_um, step_um, timeout_s=30) -> int:
        self.last_flyscan = (start_pos_um, end_pos_um, step_um)
        time.sleep(0.05)
        return self.expected_count

    def move_to_position(self, index, timeout_s=10):
        self.last_move_index = index
        time.sleep(0.01)

    def process_complete(self):
        self.completed = True


class SimCamera:
    """模拟相机：start_grabbing 后按固定间隔产出 n 帧全零图。"""

    def __init__(self, n: int, interval_s: float = 0.002):
        self._n = n
        self._interval = interval_s
        self._callback = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def open(self):
        pass

    def close(self):
        self.stop_grabbing()

    def set_exposure(self, value_us):
        pass

    def set_gain(self, value_db):
        pass

    def set_binning(self, h_bin, v_bin):
        pass

    def set_trigger_mode(self, mode="off"):
        pass

    def capture_frame(self, timeout_ms=1000):
        """返回一张占位图，供AI策略离线验证接口。"""

        return np.zeros((64, 64, 3), dtype=np.uint8)

    def register_frame_callback(self, callback):
        self._callback = callback

    def start_grabbing(self):
        def emit():
            for _ in range(self._n):
                if self._stop.is_set():
                    break
                if self._callback is not None:
                    self._callback(np.zeros((64, 64, 3), dtype=np.uint8))
                time.sleep(self._interval)

        self._stop.clear()
        self._thread = threading.Thread(target=emit, daemon=True)
        self._thread.start()

    def stop_grabbing(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


class ScoreMapEvaluator:
    """模拟评价器：按调用顺序返回预置全扫分数（忽略图像内容）。"""

    def __init__(self, scores: List[float]):
        self._scores = list(scores)
        self._counter = 0

    def evaluate_image(self, img, roi=None) -> float:
        idx = self._counter
        self._counter += 1
        if idx >= len(self._scores):
            raise IndexError(f"模拟分数越界: 第 {idx} 次评价，只有 {len(self._scores)} 个分数")
        return float(self._scores[idx])

    def reset(self):
        self._counter = 0
