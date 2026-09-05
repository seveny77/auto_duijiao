# -*- coding: utf-8 -*-
"""实时预览生命周期服务。"""

import logging
import threading
import time

from PyQt5.QtCore import QObject, Qt, pyqtSignal

from gui.app.workers.live_view_worker import LiveViewWorker


logger = logging.getLogger(__name__)


class LiveViewService(QObject):
    """管理实时预览 Worker、线程、停止事件和界面状态。"""

    # 一次实时预览任务彻底结束时发出。
    settled = pyqtSignal()

    def __init__(
        self,
        project_root: str,
        image_widget,
        live_btn,
        status_fn,
        camera_fn=None,
    ):
        super().__init__()

        self._project_root = project_root
        self._image_widget = image_widget
        self._live_btn = live_btn
        self._status_fn = status_fn
        # 返回CameraService的常驻相机句柄；未连接时为None，
        # 预览走自开自关的旧路径。
        self._camera_fn = camera_fn

        self._worker = None
        self._thread = None
        self._stop_event = None
        self._active = False #预览是否已启动
        self._last_frame_ts = 0.0

    @property
    def is_active(self) -> bool:
        """界面上的实时预览是否处于启动状态。"""

        return self._active

    @property
    def is_running(self) -> bool:
        """实时预览线程是否仍在执行。"""

        return self._thread is not None and self._thread.is_alive()

    def toggle(self, source: str, camera_params: dict):
        """根据当前状态启动或停止实时预览。"""

        if self._active or self.is_running or self._worker is not None:
            return self.stop()

        return self.start(source, camera_params)

    def start(self, source: str, camera_params: dict) -> bool:
        """创建 LiveViewWorker，并启动实时预览线程。"""

        if self.is_running:
            logger.warning("实时预览线程已经在运行")
            return False

        # 上一个 Worker 的 settled 信号还没有被 GUI 线程处理。
        if self._worker is not None:
            logger.warning("上一次实时预览仍在收尾，请稍候")
            return False

        stop_event = threading.Event()
        resident_camera = (
            self._camera_fn() if self._camera_fn is not None else None
        )
        worker = LiveViewWorker(
            source=source,
            project_root=self._project_root,
            stop_event=stop_event,
            camera_params=dict(camera_params or {}),
            camera=resident_camera,
        )

        worker.frame.connect(
            self._on_frame,
            Qt.QueuedConnection,
        )
        worker.fps.connect(
            self._on_fps,
            Qt.QueuedConnection,
        )
        worker.state.connect(
            self._on_state,
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

        thread = threading.Thread(
            target=worker.start,
            daemon=True,
            name="live-view",
        )

        # 先保存全部引用，再启动线程。
        # 防止线程启动太快，信号到达时 Service 还没有记录 Worker。
        self._worker = worker
        self._thread = thread
        self._stop_event = stop_event
        self._active = True
        self._last_frame_ts = 0.0
        self._image_widget.set_live_fps(None)

        self._live_btn.setEnabled(True)
        self._live_btn.setText("停止实时预览")
        self._status_fn("正在启动实时预览")

        logger.info("开始实时预览（%s 源）", source)
        thread.start()
        return True

    def stop(self, timeout_s: float = 3.0) -> bool:
        """请求停止实时预览，并等待线程安全退出。"""

        thread = self._thread

        if thread is None:
            self._active = False
            self._show_stopped_state()
            return True

        logger.info("正在停止实时预览...")

        self._live_btn.setEnabled(False)
        self._live_btn.setText("正在停止...")

        if self._stop_event is not None:
            self._stop_event.set()

        thread.join(timeout=timeout_s)

        if thread.is_alive():
            logger.warning(
                "实时预览线程在 %.1f 秒内未退出",
                timeout_s,
            )
            self._status_fn("实时预览仍在停止中，请稍候")
            return False

        # 线程已经退出，但 Worker 的 settled 信号可能还在 Qt 队列中。
        #
        # 这里不能提前把 self._worker 和 self._thread 清空，
        # 必须等 _on_worker_settled() 被 GUI 线程调用后再清理。
        self._active = False
        return True

    def shutdown(self, timeout_s: float = 3.0) -> bool:
        """程序关闭时停止预览；接口名称用于表达关闭语义。"""

        return self.stop(timeout_s=timeout_s)

    def _on_frame(self, image):
        """在 GUI 主线程中限频显示实时帧。"""

        if self.sender() is not self._worker:
            return

        if image is None or not self._active:
            return

        now = time.monotonic()

        # 最多约 20 FPS，防止图像转换和界面重绘占满 GUI 主线程。
        if now - self._last_frame_ts < 0.05:
            return

        self._last_frame_ts = now
        self._image_widget.show_frame(image)

    def _on_fps(self, fps: float):
        """在 GUI 主线程显示相机回调实际到达的滚动平均帧率。"""

        if self.sender() is not self._worker or not self._active:
            return
        self._image_widget.set_live_fps(fps)

    def _on_state(self, state: str):
        """在 GUI 主线程中处理 Worker 状态。"""

        if self.sender() is not self._worker:
            return

        if state == "connecting":
            logger.info("正在连接相机...")
            self._status_fn("正在连接相机")

        elif state == "started":
            self._active = True
            self._live_btn.setEnabled(True)
            self._live_btn.setText("停止实时预览")

            logger.info("实时预览已启动")
            self._status_fn("实时预览已启动")

        elif state == "stopped":
            self._active = False
            self._image_widget.set_live_fps(None)
            self._show_stopped_state()

            logger.info("实时预览已停止")
            self._status_fn("实时预览已停止")

        else:
            logger.warning("未知的实时预览状态: %s", state)

    def _on_error(self, message: str):
        """在 GUI 主线程中处理实时预览错误。"""

        if self.sender() is not self._worker:
            return

        logger.error("实时预览: %s", message)
        self._status_fn("实时预览发生错误")

        # Worker 遇到异常后会进入 finally。
        # 这里设置停止标记，确保内部循环和相机回调不再继续发送新帧。
        if self._stop_event is not None:
            self._stop_event.set()

    def _on_worker_settled(self):
        """在 settled 信号处理完成时清理 Worker 和线程引用。"""

        worker = self.sender()

        if worker is not self._worker:
            return

        # 此时 Worker.start() 已经走到最终出口，
        # 后面不再使用相机，也不再产生新的实时帧。
        self._worker = None
        self._thread = None
        self._stop_event = None
        self._active = False
        self._image_widget.set_live_fps(None)

        self._show_stopped_state()
        self.settled.emit()

    def _show_stopped_state(self):
        """把实时预览按钮恢复到停止状态。"""

        self._live_btn.setEnabled(True)
        self._live_btn.setText("开始实时预览")
