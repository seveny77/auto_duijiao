import os
import time

import numpy as np
import cv2

from PyQt5.QtCore import QObject, pyqtSignal
from backend.constants import SENSOR_W, SENSOR_H
class LiveViewWorker(QObject):
    frame = pyqtSignal(object)
    state = pyqtSignal(str)
    error = pyqtSignal(str)


    def __init__(self, source:str,project_root:str,stop_event,camera_params: dict = None):
        super().__init__()
        self._source = source
        self._project_root = project_root
        self._stop = stop_event   # threading.Event：GUI 置位来停止
        self._cam_params = camera_params or {}  # 相机参数（曝光/增益/Binning）

    def start(self):
        self._stop.clear()
        try:
            if self._source=="sim":
                self._run_sim()
            else:
                self._run_real()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.state.emit("stopped")

    def _run_sim(self):
        names = ["test_full.jpg", "test_capture.jpg",
                 "test_binning.jpg", "roi_preview.jpg"]
        paths = [os.path.join(self._project_root, n) for n in names]
        idx = 0
        while not self._stop.is_set():
            p = paths[idx % len(paths)]
            arr = np.fromfile(p, dtype=np.uint8)      # 中文路径安全读图
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                self.error.emit(f"无法读取模拟图片: {p}")
                break
            self.frame.emit(img)
            idx += 1
            time.sleep(0.05)

    def _run_real(self):
        from camera import HikCamera
        self.state.emit("connecting")
        cam = HikCamera(0)
        cam.open()
        # ★ 应用 GUI 相机参数：曝光 / 增益 / Binning
        cam.set_exposure(self._cam_params.get("exposure_us", 3000))
        cam.set_gain(self._cam_params.get("gain_db", 0.0))
        factor = self._cam_params.get("dec", 1)

        # ★ 先复位再设窗口（相机跨会话会记住旧窗口）
        cam.set_binning(1, 1)  # 复位 binning
        cam.set_decimation(1, 1)  # 复位 decimation（防残留限制窗口上限）
        cam.set_roi(0, 0, SENSOR_W, SENSOR_H)  # 全幅
        cam.set_decimation(factor, factor)  # 应用目标 binning
        w = (SENSOR_W // factor) // 4 * 4  # 该 binning 下的全幅宽（4 对齐）
        h = (SENSOR_H // factor) // 4 * 4
        cam.set_roi(0, 0, w, h)

        cam.set_trigger_mode("off")
        cam.register_frame_callback(self._on_camera_frame)
        cam.start_grabbing()
        self.state.emit("started")
        try:
            while not self._stop.is_set():
                time.sleep(0.05)
        finally:
            cam.stop_grabbing()  # 停止取流
            cam.close()  # 关闭相机

    def _on_camera_frame(self, img):
        """SDK 回调线程里执行：只发信号，绝不碰界面。"""
        if not self._stop.is_set():
            self.frame.emit(img)
