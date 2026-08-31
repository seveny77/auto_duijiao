# -*- coding: utf-8 -*-
"""相机连接生命周期服务。

与MotionService同一模式：Service持有常驻句柄（连接/断开由GUI按钮
手动触发），搜索/标定任务与实时预览只借用 camera 属性，用完不关闭。
任务期间按钮由AppController锁定，防止任务中途断开句柄。
"""

import logging
import threading

from PyQt5.QtCore import QObject, Qt, pyqtSignal

from gui.app.workers.camera_connect_worker import CameraConnectWorker


logger = logging.getLogger(__name__)


class CameraService(QObject):
    """串行管理常驻相机句柄的连接与断开。"""

    # 一次连接/断开操作彻底结束时发出（关闭协调用）。
    settled = pyqtSignal()

    def __init__(
        self,
        connect_btn,
        status_fn,
        connection_label=None,
    ):
        super().__init__()
        self._connect_btn = connect_btn
        self._status_fn = status_fn
        self._connection_label = connection_label

        self._camera = None
        self._camera_index = 0
        self._connect_worker = None
        self._connect_thread = None
        self._shutdown_requested = threading.Event()
        self._apply_connected_state(False)

    @property
    def camera(self):
        """当前常驻相机句柄；未连接时为None。调用方只借用，不关闭。"""

        return self._camera

    @property
    def camera_index(self) -> int:
        return self._camera_index

    @property
    def is_connected(self) -> bool:
        return self._camera is not None and self._camera.is_connected

    @property
    def is_connecting(self) -> bool:
        return (
            self._connect_thread is not None
            and self._connect_thread.is_alive()
        )

    def toggle(self, camera_index=0):
        if self._camera is not None:
            self.disconnect()
        else:
            self.connect(camera_index)

    def connect(self, camera_index=0):
        if self.is_connecting or self._camera is not None:
            logger.warning("相机正在连接或已连接")
            return
        self._camera_index = camera_index
        self._shutdown_requested.clear()
        self._connect_btn.setText("连接中...")
        self._connect_btn.setEnabled(False)
        self._status_fn("正在连接相机")
        logger.info("正在连接相机(index=%d)...", camera_index)

        worker = CameraConnectWorker(camera_index, self._shutdown_requested)
        worker.connected.connect(self._on_connected, Qt.QueuedConnection)
        worker.failed.connect(self._on_connect_failed, Qt.QueuedConnection)
        worker.settled.connect(self._on_connect_settled, Qt.QueuedConnection)
        thread = threading.Thread(
            target=worker.run,
            daemon=True,
            name="camera-connect",
        )
        self._connect_worker = worker
        self._connect_thread = thread
        thread.start()

    def disconnect(self):
        if self._camera is None or self.is_connecting:
            return
        camera = self._camera
        self._camera = None
        self._apply_connected_state(False)
        # close()幂等且从不抛出（内部已含停流），耗时毫秒级，
        # 因此直接在GUI线程执行，不需要命令工作线程。
        camera.close()
        self._status_fn("相机已断开")
        logger.info("相机已断开")
        self.settled.emit()

    def _on_connected(self, camera):
        worker = self.sender()
        if worker is not self._connect_worker:
            worker.close_unclaimed_camera()
            return
        if self._shutdown_requested.is_set():
            worker.close_unclaimed_camera()
            return
        claimed = worker.take_camera(expected=camera)
        if claimed is None:
            logger.error("相机句柄交接失败")
            camera.close()
            return
        self._camera = claimed
        self._apply_connected_state(True)
        self._status_fn("相机已连接")
        logger.info("相机已连接")

    def _on_connect_failed(self, message: str):
        if self.sender() is not self._connect_worker:
            return
        if not self._shutdown_requested.is_set():
            self._apply_connected_state(False)
            self._status_fn("相机连接失败")
            logger.error("相机连接失败: %s", message)

    def _on_connect_settled(self):
        worker = self.sender()
        if worker is not self._connect_worker:
            worker.close_unclaimed_camera()
            return
        worker.close_unclaimed_camera()
        self._connect_worker = None
        self._connect_thread = None
        self._connect_btn.setEnabled(True)
        self.settled.emit()

    def _apply_connected_state(self, connected: bool):
        self._connect_btn.setText("断开相机" if connected else "连接相机")
        if self._connection_label is not None:
            self._connection_label.setText(
                "已连接" if connected else "未连接"
            )

    def shutdown(self, timeout_s: float = 5.0) -> bool:
        """程序关闭时收尾：等连接线程退出并释放常驻句柄。"""

        self._shutdown_requested.set()
        thread = self._connect_thread
        if thread is not None:
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                logger.warning("相机连接线程未在限时内退出")
                return False
        if self._connect_worker is not None:
            self._connect_worker.close_unclaimed_camera()
        if self._camera is not None:
            try:
                self._camera.close()
            except Exception:
                logger.exception("关闭相机失败")
        self._connect_worker = None
        self._connect_thread = None
        self._camera = None
        self._apply_connected_state(False)
        return True
