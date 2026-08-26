# -*- coding: utf-8 -*-
"""AI 对焦模型后台加载生命周期服务。"""

import logging
import os
import threading

from PyQt5.QtCore import (
    QObject,
    Qt,
    pyqtSignal,
)

from gui.app.workers.dl_model_load_worker import (
    DLModelLoadWorker,
)


logger = logging.getLogger(__name__)


class DLModelLoadService(QObject):
    """管理 AI 模型加载线程、Worker 和 GUI 加载状态。"""

    # 一次后台加载任务彻底结束时发出。
    settled = pyqtSignal()

    def __init__(
            self,
            model_service,
            load_button,
            status_label,
            status_fn,
    ):
        super().__init__()

        self._model_service = model_service
        self._load_button = load_button
        self._status_label = status_label
        self._status_fn = status_fn

        self._worker = None
        self._thread = None

    @property
    def model(self):
        """返回当前已经成功加载的 AI 对焦模型。"""

        return self._model_service.model

    @property
    def model_path(self):
        """返回当前已经成功加载的模型路径。"""

        return self._model_service.model_path

    @property
    def is_loaded(self) -> bool:
        """当前是否持有可用的 AI 对焦模型。"""

        return self._model_service.is_loaded

    @property
    def is_loading(self) -> bool:
        """模型加载线程是否仍在运行。"""

        return (
            self._thread is not None
            and self._thread.is_alive()
        )

    def load(
            self,
            model_path: str,
    ) -> bool:
        """在后台线程中加载并预热指定模型。"""

        if self.is_loading:
            logger.warning(
                "AI 对焦模型正在加载，请勿重复操作"
            )
            self._status_fn(
                "AI 对焦模型正在加载"
            )
            return False

        resolved_path = (
            self._model_service.resolve_path(
                model_path
            )
        )

        if not resolved_path:
            self._show_failed_state(
                "AI 对焦模型路径为空"
            )
            return False

        if not os.path.isfile(resolved_path):
            self._show_failed_state(
                f"AI 对焦模型不存在: "
                f"{resolved_path}"
            )
            return False

        if self._model_service.is_current_model(
                resolved_path
        ):
            self._show_loaded_state(
                resolved_path
            )
            logger.info(
                "AI 对焦模型已经加载: %s",
                resolved_path,
            )
            return True

        self._load_button.setEnabled(False)
        self._status_label.setText(
            "正在加载..."
        )
        self._status_fn(
            "正在加载 AI 对焦模型"
        )

        logger.info(
            "开始后台加载 AI 对焦模型: %s",
            resolved_path,
        )

        worker = DLModelLoadWorker(
            model_service=self._model_service,
            model_path=resolved_path,
        )

        worker.loaded.connect(
            self._on_loaded,
            Qt.QueuedConnection,
        )
        worker.failed.connect(
            self._on_failed,
            Qt.QueuedConnection,
        )
        worker.settled.connect(
            self._on_settled,
            Qt.QueuedConnection,
        )

        thread = threading.Thread(
            target=worker.run,
            name="dl-model-load",
            daemon=False,
        )

        # 在线程启动前保存引用。
        #
        # 如果 Worker 很快完成，排队信号回到 GUI 主线程时，
        # Service 仍然能够识别信号属于当前任务。
        self._worker = worker
        self._thread = thread

        thread.start()

        return True

    def _on_loaded(
            self,
            model,
            model_path: str,
    ):
        """在 GUI 主线程中处理模型加载成功。"""

        if self.sender() is not self._worker:
            return

        if model is not self._model_service.model:
            logger.error(
                "AI 模型加载完成，但模型对象交接不一致"
            )
            self._show_failed_state(
                "AI 对焦模型状态异常"
            )
            return

        self._show_loaded_state(
            model_path
        )

        logger.info(
            "AI 对焦模型可用于搜索: %s",
            model_path,
        )

    def _on_failed(
            self,
            message: str,
    ):
        """在 GUI 主线程中处理模型加载失败。"""

        if self.sender() is not self._worker:
            return

        logger.error(message)

        if self._model_service.is_loaded:
            # 加载新模型失败时，DLModelService 会保留之前成功的模型。
            old_name = os.path.basename(
                self._model_service.model_path
                or ""
            )

            self._status_label.setText(
                f"新模型失败，仍使用: {old_name}"
            )
            self._status_fn(
                "新 AI 模型加载失败，"
                "继续保留原模型"
            )

        else:
            self._show_failed_state(
                message
            )

    def _on_settled(self):
        """清理已经结束的 Worker 和线程引用。"""

        worker = self.sender()

        if worker is not self._worker:
            return

        self._worker = None
        self._thread = None

        self._load_button.setEnabled(True)

        self.settled.emit()

    def _show_loaded_state(
            self,
            model_path: str,
    ):
        """显示模型已经可用的 GUI 状态。"""

        model_name = os.path.basename(
            model_path
        )

        self._status_label.setText(
            f"已加载: {model_name}"
        )
        self._status_fn(
            "AI 对焦模型已加载"
        )

    def _show_failed_state(
            self,
            message: str,
    ):
        """显示模型不可用的 GUI 状态。"""

        self._status_label.setText(
            "加载失败"
        )
        self._status_fn(
            message
        )
        logger.error(message)

    def shutdown(
            self,
            timeout_s: float = 3.0,
    ) -> bool:
        """等待正在执行的模型加载线程自然结束。"""

        thread = self._thread

        if thread is None:
            return True

        if not thread.is_alive():
            self._worker = None
            self._thread = None
            return True

        logger.info(
            "正在等待 AI 模型加载线程结束"
        )

        thread.join(
            timeout=timeout_s
        )

        if thread.is_alive():
            logger.warning(
                "AI 模型加载线程在 %.1f 秒内未结束",
                timeout_s,
            )
            return False

        self._worker = None
        self._thread = None

        logger.info(
            "AI 模型加载线程已结束"
        )

        return True
