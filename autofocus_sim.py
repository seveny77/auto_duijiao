# -*- coding: utf-8 -*-
"""模拟硬件：不依赖相机 SDK / 真实 PLC，用于离线跑通自动对焦闭环。"""

import threading
import time
from typing import List, Optional, Tuple

import numpy as np


class FakePlcClient:
    """模拟 PLC：记录调用，飞拍直接返回预期张数。"""

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
        time.sleep(0.05)  # 模拟 PLC 运动/拍照耗时
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
