# -*- coding: utf-8 -*-
"""搜索与标定后台任务生命周期服务。"""

import logging

from PyQt5.QtCore import (
    QObject,
    QThread,
    Qt,
    pyqtSignal,
)

from gui.app.workers.verify_worker import VerifyWorker


logger = logging.getLogger(__name__)


class FocusTaskService(QObject):
    """管理 VerifyWorker、QThread 和任务信号转发。"""

    # 扫描过程预览：
    # image, phase, sequence, score
    preview = pyqtSignal(
        object,
        str,
        int,
        float,
    )

    # 最佳帧已确定、但轴回位尚未完成。
    best_frame_ready = pyqtSignal(object)

    # 搜索或标定正常返回的结果对象。
    finished = pyqtSignal(object)

    # 后台任务未捕获异常的简短文字。
    error = pyqtSignal(str)

    # 一次任务已经执行完Worker的finally。
    settled = pyqtSignal()

    def __init__(self):
        super().__init__()

        self._thread = None
        self._worker = None
        self._running = False

        self._create_worker()

    @property
    def is_running(self) -> bool:
        """当前是否有搜索或标定任务正在执行。"""

        return self._running

    @property
    def thread_is_running(self) -> bool:
        """VerifyWorker所属的QThread是否仍在运行。"""

        return (
            self._thread is not None
            and self._thread.isRunning()
        )

    def _create_worker(self):
        """创建长期存活的QThread和VerifyWorker。"""

        thread = QThread()
        worker = VerifyWorker()

        worker.moveToThread(thread)

        worker.preview.connect(
            self._on_preview,
            Qt.QueuedConnection,
        )

        worker.best_frame_ready.connect(
            self._on_best_frame_ready,
            Qt.QueuedConnection,
        )

        worker.finished.connect(
            self._on_finished,
            Qt.QueuedConnection,
        )

        worker.error.connect(
            self._on_error,
            Qt.QueuedConnection,
        )

        worker.settled.connect(
            self._on_worker_settled,
            Qt.QueuedConnection,
        )

        # QThread事件循环结束后，让Worker在所属线程中延迟销毁。
        thread.finished.connect(
            worker.deleteLater
        )

        # 必须先保存引用，再启动线程。
        self._thread = thread
        self._worker = worker

        thread.start()

        logger.info(
            "搜索/标定后台线程已启动"
        )

    def start(self, config) -> bool:
        """把一次搜索或标定任务提交给VerifyWorker。"""

        if self._running:
            logger.warning(
                "搜索或标定任务正在运行，请勿重复启动"
            )
            return False

        if (
            self._thread is None
            or not self._thread.isRunning()
            or self._worker is None
        ):
            logger.error(
                "搜索/标定后台线程不可用"
            )
            return False

        self._running = True

        # VerifyWorker属于后台QThread。
        # start信号会通过Qt事件队列触发worker.run(config)。
        self._worker.start.emit(config)

        return True

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """关闭空闲的VerifyWorker QThread。"""

        # 正在执行任务时不能直接退出QThread。
        # 应先由上层设置cancel_event，等待Worker发出settled。
        if self._running:
            logger.warning(
                "搜索/标定任务仍在运行，暂不能关闭后台线程"
            )
            return False

        thread = self._thread

        if thread is None:
            return True

        thread.quit()

        if not thread.wait(timeout_ms):
            logger.warning(
                "搜索/标定后台线程在%dms内未退出",
                timeout_ms,
            )
            return False

        self._thread = None
        self._worker = None

        logger.info(
            "搜索/标定后台线程已退出"
        )

        return True

    def _on_preview(
        self,
        image,
        phase: str,
        sequence: int,
        score: float,
    ):
        """把Worker过程预览继续转发给GUI。"""

        if self.sender() is not self._worker:
            return

        self.preview.emit(
            image,
            phase,
            sequence,
            score,
        )

    def _on_best_frame_ready(self, event):
        """把最佳帧提前结果转发给 GUI。"""

        if self.sender() is not self._worker:
            return

        self.best_frame_ready.emit(event)

    def _on_finished(self, result):
        """把Worker正常结果继续转发给GUI。"""

        if self.sender() is not self._worker:
            return

        self.finished.emit(result)

    def _on_error(self, message: str):
        """把Worker异常继续转发给GUI。"""

        if self.sender() is not self._worker:
            return

        self.error.emit(message)

    def _on_worker_settled(self):
        """Worker执行完finally后恢复空闲状态。"""

        if self.sender() is not self._worker:
            return

        self._running = False
        self.settled.emit()
