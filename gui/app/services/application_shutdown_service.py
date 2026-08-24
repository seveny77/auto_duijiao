# -*- coding: utf-8 -*-
"""应用程序资源安全关闭协调服务。"""

import logging

from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot


logger = logging.getLogger(__name__)


class ApplicationShutdownService(QObject):
    """按安全顺序协调GUI、任务线程、PLC和相机资源关闭。"""

    # 某个异步资源结束后，请求MainWindow重新触发close()。
    retry_requested = pyqtSignal()

    def __init__(
        self,
        config_service,
        live_view_service,
        focus_task_service,
        plc_service,
        controller,
        message_fn,
        status_fn,
    ):
        super().__init__()

        self._config_service = config_service
        self._live_view_service = live_view_service
        self._focus_task_service = focus_task_service
        self._plc_service = plc_service
        self._controller = controller
        self._message_fn = message_fn
        self._status_fn = status_fn

        # 用户是否已经提出关闭请求，但资源尚未完全释放。
        self._pending = False

        # 防止多个settled信号在同一时刻重复安排close()。
        self._retry_scheduled = False

        # 防止资源已经释放后再次重复执行shutdown。
        self._shutdown_complete = False

        self._live_view_service.settled.connect(
            self._on_dependency_settled,
            Qt.QueuedConnection,
        )
        self._focus_task_service.settled.connect(
            self._on_dependency_settled,
            Qt.QueuedConnection,
        )
        self._plc_service.settled.connect(
            self._on_dependency_settled,
            Qt.QueuedConnection,
        )

    @property
    def is_pending(self) -> bool:
        """是否存在等待资源结束的关闭请求。"""

        return self._pending

    @property
    def is_complete(self) -> bool:
        """全部应用资源是否已经完成关闭。"""

        return self._shutdown_complete

    def try_shutdown(self) -> bool:
        """尝试按安全顺序释放资源。

        返回True表示窗口可以关闭；
        返回False表示还有资源正在退出，窗口应暂缓关闭。
        """

        if self._shutdown_complete:
            return True

        # 只在用户第一次提出关闭请求时保存一次配置。
        if not self._pending:
            self._config_service.save()

        # 第一步：停止实时预览。
        if not self._live_view_service.shutdown():
            self._defer(
                "[提示] 实时预览尚未完全停止，"
                "预览结束后窗口将自动关闭",
                "正在停止实时预览，完成后将自动关闭",
            )
            return False

        # 第二步：取消并等待搜索或标定任务。
        if self._controller.state == self._controller.STATE_RUNNING:
            self._controller.cancel()

            self._defer(
                "[提示] 已请求停止后台任务，"
                "任务安全结束后窗口将自动关闭",
                "正在停止后台任务，完成后将自动关闭",
            )
            return False

        # 第三步：关闭空闲的搜索/标定任务线程。
        if not self._focus_task_service.shutdown():
            self._defer(
                "[提示] 搜索/标定后台线程尚未完全停止，"
                "任务结束后窗口将自动关闭",
                "正在停止后台任务线程，完成后将自动关闭",
            )
            return False

        # 第四步：等待PLC连接线程并释放PLC。
        if not self._plc_service.shutdown():
            self._defer(
                "[提示] PLC连接线程尚未完全停止，"
                "连接任务结束后窗口将自动关闭",
                "正在停止PLC连接任务，完成后将自动关闭",
            )
            return False

        # 前面的资源使用线程都已退出，
        # 此时才允许反初始化相机SDK。
        from camera.camera_adapter import HikCamera

        HikCamera.shutdown()

        self._pending = False
        self._shutdown_complete = True

        logger.info(
            "程序资源清理完成，正在关闭窗口"
        )

        return True

    def _defer(
        self,
        message: str,
        status: str,
    ):
        """记录关闭等待状态，并避免重复输出提示日志。"""

        first_pending_request = not self._pending
        self._pending = True

        if first_pending_request:
            self._message_fn(message)

        self._status_fn(status)

    @pyqtSlot()
    def _on_dependency_settled(self):
        """某个异步服务结束后，安排下一轮关闭尝试。"""

        if not self._pending:
            return

        if self._retry_scheduled:
            return

        self._retry_scheduled = True

        # 不在当前settled槽中立刻关闭窗口，
        # 而是安排到GUI事件循环的下一轮。
        QTimer.singleShot(
            0,
            self._emit_retry_request,
        )

    def _emit_retry_request(self):
        """发出重新关闭窗口的请求。"""

        self._retry_scheduled = False

        if self._pending:
            self.retry_requested.emit()