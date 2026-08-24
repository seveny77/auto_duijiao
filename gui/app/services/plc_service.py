# -*- coding: utf-8 -*-
"""PLC 连接生命周期服务。"""

import logging
import threading

from PyQt5.QtCore import QObject, Qt, pyqtSignal

from gui.app.workers.plc_connect_worker import PlcConnectWorker


logger = logging.getLogger(__name__)


class PlcService(QObject):
    """管理 PLC 连接线程、客户端和 GUI 状态。"""

    # 一次 PLC 连接任务彻底结束时发出。
    # 后面 MainWindow 可以用它继续待处理的关闭请求。
    settled = pyqtSignal()

    def __init__(self, connect_btn, stroke_label, status_fn):
        super().__init__()

        self._connect_btn = connect_btn
        self._stroke_label = stroke_label
        self._status_fn = status_fn

        self._client = None
        self._stroke_range = None

        self._worker = None
        self._thread = None
        self._shutdown_requested = threading.Event()

    @property
    def client(self):
        """返回当前正式接管的 PlcClient；未连接时返回 None。"""

        return self._client

    @property
    def stroke_range(self):
        """
        返回 PLC 当前行程范围。

        已连接并成功读取行程时：
            (最小位置, 最大位置)

        未连接时：
            None
        """

        return self._stroke_range

    @property
    def is_connecting(self) -> bool:
        """PLC 连接线程是否仍在执行。"""

        return self._thread is not None and self._thread.is_alive()

    def toggle(self, host: str, port: int):
        """未连接时执行连接，已连接时执行断开。"""

        if self._client is not None:
            self.disconnect()
        else:
            self.connect(host, port)

    def connect(self, host: str, port: int):
        """创建 Worker，并在 Python 后台线程中执行连接。"""

        if self.is_connecting:
            logger.warning("PLC 正在连接，请勿重复操作")
            return

        self._shutdown_requested.clear()

        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("连接中...")
        self._status_fn("正在连接 PLC")

        logger.info("正在连接 PLC %s:%d ...", host, port)

        worker = PlcConnectWorker(
            host=host,
            port=port,
            stop_event=self._shutdown_requested,
        )
        worker.connected.connect(
            self._on_connected,
            Qt.QueuedConnection,
        )
        worker.failed.connect(
            self._on_failed,
            Qt.QueuedConnection,
        )
        worker.settled.connect(
            self._on_worker_settled,
            Qt.QueuedConnection,
        )

        thread = threading.Thread(
            target=worker.run,
            daemon=True,
            name="plc-connect",
        )

        # 必须保存引用，后面才能判断、等待和清理。
        self._worker = worker
        self._thread = thread

        thread.start()

    def _on_connected(self, payload):
        """在 GUI 主线程中接管 Worker 创建的 PlcClient。"""

        worker = self.sender()
        plc, stroke = payload

        # 防止已经过期的 Worker 把旧连接交给当前 Service。
        if worker is not self._worker:
            worker.close_unclaimed_client()
            return

        # 信号排队期间，程序可能已经进入关闭流程。
        if self._shutdown_requested.is_set():
            worker.close_unclaimed_client()
            return

        client = worker.take_client(expected=plc)

        if client is None:
            logger.error("PLC 客户端交接失败")
            try:
                plc.disconnect()
            except Exception:
                logger.exception("PLC 客户端交接失败后的清理异常")
            return

        self._client = client

        self._stroke_range = stroke

        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("断开 PLC")
        self._stroke_label.setText(
            f"行程: {stroke[0]} ~ {stroke[1]} µm"
        )

        logger.info(
            "PLC 行程范围: %s ~ %s µm",
            stroke[0],
            stroke[1],
        )
        self._status_fn("PLC 已连接")

    def _on_failed(self, message: str):
        """在 GUI 主线程中处理 Worker 返回的连接失败。"""

        if self.sender() is not self._worker:
            return

        if self._shutdown_requested.is_set():
            return

        self._show_disconnected_state()
        logger.error("PLC 连接失败: %s", message)
        self._status_fn("PLC 连接失败")

    def _on_worker_settled(self):
        """清理已经结束的 Worker 和线程引用。"""

        worker = self.sender()

        # 如果这是已经过期的 Worker，只清理它可能遗留的客户端。
        if worker is not self._worker:
            worker.close_unclaimed_client()
            return

        worker.close_unclaimed_client()

        self._worker = None
        self._thread = None

        if self._client is None and not self._shutdown_requested.is_set():
            self._show_disconnected_state()

        # 通知外部：PLC 连接任务已经完全结束。
        self.settled.emit()

    def disconnect(self):
        """断开当前正式连接，并恢复未连接界面状态。"""

        plc = self._client
        self._client = None

        if plc is not None:
            try:
                plc.disconnect()
            except Exception:
                logger.exception("断开 PLC 异常")

        self._show_disconnected_state()
        self._status_fn("PLC 已断开")
        logger.info("PLC 已断开")

    def shutdown(self, timeout_s: float = 3.5) -> bool:
        """程序关闭时等待 Worker 结束，并释放全部 PLC 资源。"""

        self._shutdown_requested.set()

        thread = self._thread
        worker = self._worker

        if thread is not None:
            thread.join(timeout=timeout_s)

            if thread.is_alive():
                logger.warning(
                    "PLC 连接线程在 %.1f 秒内未退出",
                    timeout_s,
                )
                return False

        # 线程已经退出，可以安全清理尚未交接的客户端。
        if worker is not None:
            worker.close_unclaimed_client()

        self.disconnect()
        return True

    def _show_disconnected_state(self):
        """把 PLC 相关控件恢复到未连接状态。"""
        self._stroke_range = None

        self._connect_btn.setEnabled(True)
        self._connect_btn.setText("连接 PLC")
        self._stroke_label.setText("行程: 未连接")