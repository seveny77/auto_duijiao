import os
import time

import numpy as np
import cv2

from PyQt5.QtCore import QObject, pyqtSignal
from backend.camera_utils import set_coarse_frame
class LiveViewWorker(QObject):
    frame = pyqtSignal(object)
    state = pyqtSignal(str)
    error = pyqtSignal(str)
    settled = pyqtSignal() #退出阻断信号


    def __init__(self, source:str,project_root:str,stop_event,camera_params: dict = None):
        super().__init__()
        self._source = source
        self._project_root = project_root
        self._stop = stop_event   # threading.Event：GUI 置位来停止
        self._cam_params = camera_params or {}  # 相机参数（曝光/增益/Binning）

    def start(self):
        """运行实时预览任务。

        停止事件由 MainWindow 创建和控制，
        Worker 只负责检查，不擅自清除停止请求。
        """

        try:
            # 如果线程刚启动，GUI 就已经发送了停止请求，
            # 那么不再进入模拟预览或真实相机初始化。
            if self._stop.is_set():
                return

            if self._source == "sim":
                self._run_sim()
            else:
                self._run_real()

        except Exception as e:
            self.error.emit(str(e))

        finally:
            # 无论正常停止还是发生异常，
            # 都通知 GUI：本次预览任务已经走到结束阶段。
            self.state.emit("stopped")
            self.settled.emit()

    def _run_sim(self):
        """循环显示项目中现有的模拟图片。"""

        names = [
            "test_full.jpg",
            "test_capture.jpg",
            "test_binning.jpg",
            "roi_preview.jpg",
        ]

        paths = [
            os.path.join(self._project_root, name)
            for name in names
        ]

        # 项目中不一定同时保留全部测试图片。
        # 只使用当前真实存在的文件，避免因为第一张图缺失就退出预览。
        paths = [
            path
            for path in paths
            if os.path.isfile(path)
        ]

        if not paths:
            raise FileNotFoundError(
                "没有找到可用于实时预览的模拟图片"
            )

        self.state.emit("started")

        idx = 0

        while not self._stop.is_set():
            path = paths[idx % len(paths)]

            # np.fromfile + cv2.imdecode 可以兼容中文目录。
            array = np.fromfile(
                path,
                dtype=np.uint8,
            )

            image = cv2.imdecode(
                array,
                cv2.IMREAD_COLOR,
            )

            if image is None:
                self.error.emit(
                    f"无法读取模拟图片: {path}"
                )
                break

            self.frame.emit(image)

            idx += 1
            time.sleep(0.05)

    def _run_real(self):
        """运行真实相机预览，并保证相机最终一定关闭。"""

        from camera import HikCamera

        self.state.emit("connecting")

        # 先创建 Python 相机对象。
        #
        # 此时只是创建包装对象，还没有真正打开硬件相机。
        cam = HikCamera(0)

        try:
            # 如果连接相机之前已经收到停止请求，
            # 就不再执行耗时的相机初始化。
            if self._stop.is_set():
                return

            cam.open()

            # 应用 GUI 中设置的曝光和增益。
            cam.set_exposure(
                self._cam_params.get("exposure_us", 3000)
            )

            cam.set_gain(
                self._cam_params.get("gain_db", 0.0)
            )

            factor = self._cam_params.get("dec", 1)

            set_coarse_frame(
                cam,
                mode="decimation",
                factor=factor,
            )

            # 实时预览使用自由运行模式：
            # 相机连续曝光，不等待 PLC 硬件触发。
            cam.set_trigger_mode("off")

            # 注册回调。每到一帧图像，SDK 会调用
            # self._on_camera_frame(image)。
            cam.register_frame_callback(
                self._on_camera_frame
            )

            # 如果停止请求是在相机初始化过程中到达的，
            # 就不要再启动取流。
            #
            # 函数 return 以后，下面的 finally 仍然会关闭相机。
            if self._stop.is_set():
                return

            cam.start_grabbing()
            self.state.emit("started")

            # 预览线程不主动获取图像。
            #
            # 图像由相机 SDK 回调送入 _on_camera_frame()，
            # 这个循环只负责维持线程存活并等待停止请求。
            while not self._stop.is_set():
                time.sleep(0.05)

        finally:
            # close() 内部已经包含：
            #
            #   正在取流时调用 stop_grabbing()
            #       ↓
            #   CloseDevice()
            #       ↓
            #   DestroyHandle()
            #
            # 所以这里不需要先单独调用 stop_grabbing()。
            cam.close()

    def _on_camera_frame(self, img):
        """SDK 回调线程里执行：只发信号，绝不碰界面。"""
        if not self._stop.is_set():
            self.frame.emit(img)
