# -*- coding: utf-8 -*-
"""最终成像质检任务的异步调度和生命周期服务。"""

import copy

from PyQt5.QtCore import QObject, QThread, Qt, pyqtSignal

from backend.circle_detection import HoughCircleDetector
from backend.inspection_engine import InspectionRuleEngine
from backend.inspection_types import InspectionResult
from backend.segmentation_model_service import SegmentationModelService
from gui.app.workers.inspection_worker import InspectionWorker


class InspectionState:
    NOT_LOADED = "NOT_LOADED"
    LOADING = "LOADING"
    READY = "READY"
    RUNNING = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    ERROR = "ERROR"


class InspectionService(QObject):
    """管理独立检测线程、单槽待检测图和对外 Qt 信号。"""

    state_changed = pyqtSignal(str)
    model_loading = pyqtSignal(str)
    model_loaded = pyqtSignal(str, object)
    model_load_failed = pyqtSignal(str)
    image_pending = pyqtSignal(str)
    inspection_started = pyqtSignal(str)
    image_inspection_finished = pyqtSignal(str, object)
    image_inspection_visual_ready = pyqtSignal(str, object, object, object)
    inspection_finished = pyqtSignal(str, object)
    inspection_visual_ready = pyqtSignal(str, object, object, object)
    circle_redetection_started = pyqtSignal(str)
    image_circle_redetection_finished = pyqtSignal(str, object)
    circle_redetection_finished = pyqtSignal(str, object)
    shutdown_ready = pyqtSignal()

    _load_requested = pyqtSignal(str, object)
    _inspection_requested = pyqtSignal(str, object, object)
    _circle_redetection_requested = pyqtSignal(str, object, object, object)
    _shutdown_requested = pyqtSignal()

    def __init__(
        self,
        segmentation_service=None,
        circle_detector=None,
        rule_engine=None,
        parent=None,
    ):
        super().__init__(parent)

        self._state = InspectionState.NOT_LOADED
        self._model_ready = False
        self._shutting_down = False
        self._shutdown_signal_sent = False
        self._shutdown_complete = False
        self._task_sequence = 0
        self._active_operation = None
        self._active_task_id = None
        self._active_image_identity = None
        self._active_image = None
        self._active_config = None
        self._pending_task = None

        self._thread = QThread(self)
        self._worker = InspectionWorker(
            segmentation_service or SegmentationModelService(),
            circle_detector or HoughCircleDetector(),
            rule_engine or InspectionRuleEngine(),
        )
        self._worker.moveToThread(self._thread)

        self._load_requested.connect(
            self._worker.load_model,
            Qt.QueuedConnection,
        )
        self._inspection_requested.connect(
            self._worker.inspect,
            Qt.QueuedConnection,
        )
        self._circle_redetection_requested.connect(
            self._worker.redetect_circle,
            Qt.QueuedConnection,
        )
        self._shutdown_requested.connect(
            self._worker.shutdown,
            Qt.QueuedConnection,
        )
        self._worker.model_loaded.connect(
            self._on_model_loaded,
            Qt.QueuedConnection,
        )
        self._worker.model_load_failed.connect(
            self._on_model_load_failed,
            Qt.QueuedConnection,
        )
        self._worker.image_inspection_finished.connect(
            self._on_image_inspection_finished,
            Qt.QueuedConnection,
        )
        self._worker.inspection_finished.connect(
            self._on_inspection_finished,
            Qt.QueuedConnection,
        )
        self._worker.image_circle_redetection_finished.connect(
            self._on_image_circle_redetection_finished,
            Qt.QueuedConnection,
        )
        self._worker.circle_redetection_finished.connect(
            self._on_circle_redetection_finished,
            Qt.QueuedConnection,
        )
        self._worker.shutdown_complete.connect(
            self._on_worker_shutdown_complete,
            Qt.QueuedConnection,
        )
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(
            self._on_thread_finished,
            Qt.QueuedConnection,
        )
        self._thread.start()

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_model_loaded(self) -> bool:
        return self._model_ready

    @property
    def has_pending_image(self) -> bool:
        return self._pending_task is not None

    @property
    def is_running(self) -> bool:
        return self._state in (InspectionState.LOADING, InspectionState.RUNNING)

    @property
    def thread_is_running(self) -> bool:
        return self._thread.isRunning()

    @property
    def is_shutdown_complete(self) -> bool:
        return self._shutdown_complete

    def load_model(self, model_path: str, config) -> bool:
        """异步提交唯一模型加载请求。"""

        if self._shutting_down:
            return False
        if self._state == InspectionState.LOADING:
            return False
        if self._model_ready:
            raise RuntimeError("检测模型已经加载，重启软件后才能更换模型")

        config_snapshot = copy.deepcopy(config)
        self._set_state(InspectionState.LOADING)
        self.model_loading.emit(str(model_path))
        self._load_requested.emit(str(model_path), config_snapshot)
        return True

    def submit_image(self, image, config) -> str:
        """提交最终图；忙碌或模型未加载时只保留最新一张。"""

        if self._shutting_down:
            return ""
        if image is None or getattr(image, "ndim", 0) not in (2, 3):
            raise ValueError("提交质检的图像无效")
        if getattr(image, "size", 0) == 0:
            raise ValueError("提交质检的图像为空")

        image_identity = id(image)
        if image_identity in (
            self._active_image_identity,
            self._pending_task[3] if self._pending_task is not None else None,
        ):
            return ""

        task_id = self._new_task_id()
        task = (
            task_id,
            image,
            copy.deepcopy(config),
            image_identity,
        )
        if not self._model_ready or self._state != InspectionState.READY:
            self._pending_task = task
            self.image_pending.emit(task_id)
            return task_id

        self._start_task(task)
        return task_id

    def redetect_circle(
        self,
        task_id: str,
        image,
        source_result: InspectionResult,
        config,
    ) -> bool:
        """后台重跑当前图 Hough，并按新圆心重新裁切 ROI 和推理。"""

        if self._shutting_down or self._state != InspectionState.READY:
            return False
        if not self._model_ready:
            return False
        if not str(task_id).strip():
            raise ValueError("重新找圆任务号不能为空")
        if image is None or getattr(image, "ndim", 0) not in (2, 3):
            raise ValueError("重新找圆的图像无效")
        if getattr(image, "size", 0) == 0:
            raise ValueError("重新找圆的图像为空")
        if not isinstance(source_result, InspectionResult):
            raise TypeError("source_result 必须是 InspectionResult")

        config_snapshot = copy.deepcopy(config)
        self._active_operation = "circle_redetection"
        self._active_task_id = str(task_id)
        self._active_image_identity = id(image)
        self._active_image = image
        self._active_config = config_snapshot
        self._set_state(InspectionState.RUNNING)
        self.circle_redetection_started.emit(str(task_id))
        self._circle_redetection_requested.emit(
            str(task_id),
            image,
            source_result,
            config_snapshot,
        )
        return True

    def begin_shutdown(self) -> bool:
        """停止接收任务并异步等待 Worker 自然清理。"""

        if self._shutdown_complete:
            return True
        if self._shutting_down:
            return False

        self._shutting_down = True
        self._pending_task = None
        self._set_state(InspectionState.SHUTTING_DOWN)
        self._request_worker_shutdown()
        return False

    def _new_task_id(self) -> str:
        self._task_sequence += 1
        return f"inspection-{self._task_sequence:06d}"

    def _start_task(self, task):
        task_id, image, config_snapshot, image_identity = task
        self._pending_task = None
        self._active_operation = "inspection"
        self._active_task_id = task_id
        self._active_image_identity = image_identity
        self._active_image = image
        self._active_config = config_snapshot
        self._set_state(InspectionState.RUNNING)
        self.inspection_started.emit(task_id)
        self._inspection_requested.emit(task_id, image, config_snapshot)

    def _set_state(self, state: str):
        if self._state == state:
            return
        self._state = state
        self.state_changed.emit(state)

    def _on_model_loaded(self, model_path: str, metadata):
        if self.sender() is not self._worker:
            return
        self._model_ready = True
        self.model_loaded.emit(model_path, metadata)
        if self._shutting_down:
            self._request_worker_shutdown()
        elif self._pending_task is not None:
            self._set_state(InspectionState.READY)
            self._start_task(self._pending_task)
        else:
            self._set_state(InspectionState.READY)

    def _on_model_load_failed(self, message: str):
        if self.sender() is not self._worker:
            return
        self._model_ready = False
        self.model_load_failed.emit(message)
        if self._shutting_down:
            self._request_worker_shutdown()
        else:
            self._set_state(InspectionState.NOT_LOADED)

    def _on_inspection_finished(self, task_id: str, result):
        if self.sender() is not self._worker:
            return
        if (
            self._active_operation != "inspection"
            or task_id != self._active_task_id
        ):
            return

        completed_image = self._active_image
        completed_config = self._active_config
        self._clear_active_operation()
        self.inspection_finished.emit(task_id, result)
        self.inspection_visual_ready.emit(
            task_id,
            completed_image,
            result,
            completed_config,
        )

        if self._shutting_down:
            self._request_worker_shutdown()
        elif self._pending_task is not None:
            self._set_state(InspectionState.READY)
            self._start_task(self._pending_task)
        else:
            self._set_state(InspectionState.READY)

    def _on_image_inspection_finished(self, task_id: str, result):
        """在旧 GUI 兼容结果之前转发完整多圆结果。"""

        if self.sender() is not self._worker:
            return
        if (
            self._active_operation != "inspection"
            or task_id != self._active_task_id
        ):
            return
        self.image_inspection_finished.emit(task_id, result)
        self.image_inspection_visual_ready.emit(
            task_id,
            self._active_image,
            result,
            self._active_config,
        )

    def _on_circle_redetection_finished(self, task_id: str, result):
        if self.sender() is not self._worker:
            return
        if (
            self._active_operation != "circle_redetection"
            or task_id != self._active_task_id
        ):
            return

        self._clear_active_operation()
        self.circle_redetection_finished.emit(task_id, result)
        if self._shutting_down:
            self._request_worker_shutdown()
        elif self._pending_task is not None:
            self._set_state(InspectionState.READY)
            self._start_task(self._pending_task)
        else:
            self._set_state(InspectionState.READY)

    def _on_image_circle_redetection_finished(self, task_id: str, result):
        """转发重新找圆后重新推理得到的完整多圆结果。"""

        if self.sender() is not self._worker:
            return
        if (
            self._active_operation != "circle_redetection"
            or task_id != self._active_task_id
        ):
            return
        self.image_circle_redetection_finished.emit(task_id, result)

    def _clear_active_operation(self):
        self._active_operation = None
        self._active_task_id = None
        self._active_image_identity = None
        self._active_image = None
        self._active_config = None

    def _request_worker_shutdown(self):
        if self._shutdown_signal_sent:
            return
        self._shutdown_signal_sent = True
        self._shutdown_requested.emit()

    def _on_worker_shutdown_complete(self):
        if self.sender() is not self._worker:
            return
        self._thread.quit()

    def _on_thread_finished(self):
        self._shutdown_complete = True
        self.shutdown_ready.emit()
