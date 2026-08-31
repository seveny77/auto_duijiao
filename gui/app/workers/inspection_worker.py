# -*- coding: utf-8 -*-
"""最终成像质检后台 Worker。"""

import time

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from backend.inspection_types import InspectionResult, InspectionStatus


class InspectionWorker(QObject):
    """在所属 QThread 中串行执行模型加载、推理、找圆和判定。"""

    model_loaded = pyqtSignal(str, object)
    model_load_failed = pyqtSignal(str)
    inspection_finished = pyqtSignal(str, object)
    circle_redetection_finished = pyqtSignal(str, object)
    shutdown_complete = pyqtSignal()

    def __init__(
        self,
        segmentation_service,
        circle_detector,
        rule_engine,
    ):
        super().__init__()
        self._segmentation_service = segmentation_service
        self._circle_detector = circle_detector
        self._rule_engine = rule_engine

    @pyqtSlot(str, object)
    def load_model(self, model_path: str, config):
        """在当前后台线程加载并预热唯一分割模型。"""

        try:
            self._segmentation_service.load(
                model_path,
                imgsz=config.inference_imgsz,
                confidence_floor=config.inference_confidence_floor,
            )
            metadata = {
                "class_names": self._segmentation_service.class_names,
                "load_ms": self._segmentation_service.load_ms,
                "warmup_ms": self._segmentation_service.warmup_ms,
            }
            self.model_loaded.emit(
                self._segmentation_service.model_path,
                metadata,
            )
        except Exception as error:
            self.model_load_failed.emit(_error_message(error))

    @pyqtSlot(str, object, object)
    def inspect(self, task_id: str, image, config):
        """执行一次完整的分割、Hough 找圆和初始规则判定。"""

        total_start = time.perf_counter()
        try:
            image_height, image_width = _image_size(image)

            inference_start = time.perf_counter()
            instances = self._segmentation_service.predict(
                image,
                imgsz=config.inference_imgsz,
                confidence_floor=config.inference_confidence_floor,
            )
            inference_ms = _elapsed_ms(inference_start)

            circle_start = time.perf_counter()
            (
                candidates,
                selected_index,
                confirmed,
                circle_warnings,
            ) = self._circle_detector.detect(image, config.circle)
            circle_ms = _elapsed_ms(circle_start)

            evaluation_start = time.perf_counter()
            result = self._rule_engine.evaluate(
                instances=instances,
                circle_candidates=candidates,
                selected_circle_index=selected_index,
                circle_confirmed=confirmed,
                mm_per_pixel=config.mm_per_pixel,
                region_rules=config.region_rules,
                image_width=image_width,
                image_height=image_height,
            )
            evaluation_ms = _elapsed_ms(evaluation_start)

            for warning in circle_warnings:
                if warning not in result.warnings:
                    result.warnings.append(warning)
            result.timings_ms.update({
                "inference": inference_ms,
                "circle_detection": circle_ms,
                "evaluation": evaluation_ms,
                "total": _elapsed_ms(total_start),
            })
        except Exception as error:
            result = InspectionResult(
                status=InspectionStatus.ERROR,
                error=_error_message(error),
                timings_ms={"total": _elapsed_ms(total_start)},
            )

        self.inspection_finished.emit(task_id, result)

    @pyqtSlot(str, object, object, object)
    def redetect_circle(self, task_id: str, image, source_result, config):
        """复用分割实例，仅重新执行 Hough 和规则判定。"""

        total_start = time.perf_counter()
        try:
            circle_start = time.perf_counter()
            (
                candidates,
                selected_index,
                confirmed,
                circle_warnings,
            ) = self._circle_detector.detect(image, config.circle)
            circle_ms = _elapsed_ms(circle_start)

            evaluation_start = time.perf_counter()
            result = self._rule_engine.evaluate(
                instances=list(source_result.instances),
                circle_candidates=candidates,
                selected_circle_index=selected_index,
                circle_confirmed=confirmed,
                mm_per_pixel=config.mm_per_pixel,
                region_rules=config.region_rules,
                image_width=source_result.image_width,
                image_height=source_result.image_height,
            )
            evaluation_ms = _elapsed_ms(evaluation_start)

            for warning in circle_warnings:
                if warning not in result.warnings:
                    result.warnings.append(warning)
            result.timings_ms = dict(source_result.timings_ms)
            result.timings_ms.update({
                "circle_detection": circle_ms,
                "reevaluation": evaluation_ms,
                "circle_redetection_total": _elapsed_ms(total_start),
            })
        except Exception as error:
            result = InspectionResult(
                status=InspectionStatus.ERROR,
                error=_error_message(error),
                image_width=int(getattr(source_result, "image_width", 0)),
                image_height=int(getattr(source_result, "image_height", 0)),
                instances=list(getattr(source_result, "instances", []) or []),
                timings_ms={
                    "circle_redetection_total": _elapsed_ms(total_start)
                },
            )

        self.circle_redetection_finished.emit(task_id, result)

    @pyqtSlot()
    def shutdown(self):
        """在 Worker 线程中释放模型引用，然后通知上层退出线程。"""

        try:
            self._segmentation_service.unload_for_shutdown()
        finally:
            self.shutdown_complete.emit()


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _image_size(image) -> tuple[int, int]:
    if image is None or getattr(image, "ndim", 0) not in (2, 3):
        raise ValueError("质检任务收到无效图像")
    if getattr(image, "size", 0) == 0:
        raise ValueError("质检任务收到空图像")
    return int(image.shape[0]), int(image.shape[1])


def _error_message(error: Exception) -> str:
    text = str(error).strip()
    if text:
        return f"{type(error).__name__}: {text}"
    return type(error).__name__
