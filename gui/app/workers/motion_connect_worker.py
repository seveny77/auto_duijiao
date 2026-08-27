# -*- coding: utf-8 -*-
"""LCT运动后端连接工作对象。"""

import logging
import threading

from PyQt5.QtCore import QObject, pyqtSignal


logger = logging.getLogger(__name__)


class MotionConnectWorker(QObject):
    """在后台执行一次M60+E4O4连接和行程读取。"""

    connected = pyqtSignal(object)
    failed = pyqtSignal(str)
    settled = pyqtSignal()

    def __init__(self, config, stop_event=None):
        super().__init__()
        self._config = config
        self._stop_event = stop_event or threading.Event()
        self._backend = None
        self._backend_lock = threading.Lock()

    def run(self):
        backend = None
        try:
            if self._stop_event.is_set():
                return

            from motion.lct import LctMotionBackend

            backend = LctMotionBackend(self._config)
            backend.connect()
            stroke = backend.read_stroke_range()
            state = backend.get_state()

            if self._stop_event.is_set():
                backend.disconnect()
                logger.info("运动控制器连接完成时程序已请求关闭，本次连接已释放")
                return

            with self._backend_lock:
                self._backend = backend

            self.connected.emit((backend, stroke, state))
            backend = None

        except Exception as exc:
            if backend is not None:
                try:
                    backend.disconnect()
                except Exception:
                    logger.exception("运动控制器连接失败后的清理异常")

            if not self._stop_event.is_set():
                self.failed.emit(str(exc))
        finally:
            self.settled.emit()

    def take_backend(self, expected=None):
        """把已经连接的后端交给MotionService。"""

        with self._backend_lock:
            backend = self._backend
            if expected is not None and backend is not expected:
                return None
            self._backend = None
            return backend

    def close_unclaimed_backend(self):
        """关闭连接成功但尚未被Service接管的后端。"""

        backend = self.take_backend()
        if backend is None:
            return
        try:
            backend.disconnect()
        except Exception:
            logger.exception("关闭未接管运动后端时发生异常")
