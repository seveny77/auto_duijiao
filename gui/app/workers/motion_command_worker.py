# -*- coding: utf-8 -*-
"""在Python后台线程中执行一次运动控制器命令。"""

import logging

from PyQt5.QtCore import QObject, pyqtSignal


logger = logging.getLogger(__name__)


class MotionCommandWorker(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, object)
    settled = pyqtSignal()

    def __init__(self, backend, command):
        super().__init__()
        self._backend = backend
        self._command = command

    def run(self):
        try:
            state = self._command()
            self.succeeded.emit(state)
        except Exception as error:
            logger.exception("运动控制命令执行失败")
            try:
                state = self._backend.get_state()
            except Exception:
                state = None
            self.failed.emit(str(error) or type(error).__name__, state)
        finally:
            self.settled.emit()
