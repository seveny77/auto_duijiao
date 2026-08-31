# -*- coding: utf-8 -*-
"""相机连接工作对象。"""

import logging
import threading

from PyQt5.QtCore import QObject, pyqtSignal


logger = logging.getLogger(__name__)


class CameraConnectWorker(QObject):
    """在后台执行一次相机打开（USB枚举+建句柄约0.5s，不阻塞GUI）。"""

    connected = pyqtSignal(object)
    failed = pyqtSignal(str)
    settled = pyqtSignal()

    def __init__(self, camera_index, stop_event=None):
        super().__init__()
        self._camera_index = camera_index
        self._stop_event = stop_event or threading.Event()
        self._camera = None
        self._camera_lock = threading.Lock()

    def run(self):
        camera = None
        try:
            if self._stop_event.is_set():
                return

            from camera import HikCamera

            camera = HikCamera(self._camera_index)
            camera.open()

            if self._stop_event.is_set():
                camera.close()
                logger.info("相机连接完成时程序已请求关闭，本次连接已释放")
                return

            with self._camera_lock:
                self._camera = camera

            self.connected.emit(camera)
            camera = None

        except Exception as exc:
            if camera is not None:
                try:
                    camera.close()
                except Exception:
                    logger.exception("相机连接失败后的清理异常")

            if not self._stop_event.is_set():
                self.failed.emit(str(exc))
        finally:
            self.settled.emit()

    def take_camera(self, expected=None):
        """把已经打开的相机交给CameraService。"""

        with self._camera_lock:
            camera = self._camera
            if expected is not None and camera is not expected:
                return None
            self._camera = None
            return camera

    def close_unclaimed_camera(self):
        """关闭连接成功但尚未被Service接管的相机。"""

        camera = self.take_camera()
        if camera is None:
            return
        try:
            camera.close()
        except Exception:
            logger.exception("关闭未接管相机时发生异常")
