# -*- coding: utf-8 -*-
"""最终成像质检后台 Worker。"""

import logging
import time

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from backend.inspection_image_store import save_inspection_image
from backend.inspection_roi import (
    build_circle_roi,
    crop_roi,
    is_roi_clipped,
    restore_instances_to_image,
)
from backend.inspection_types import (
    CircleInspectionResult,
    ImageInspectionResult,
    InspectionResult,
    InspectionStatus,
)


logger = logging.getLogger(__name__)


class InspectionWorker(QObject):
    """在所属 QThread 中串行执行模型加载、推理、找圆和判定。"""

    model_loaded = pyqtSignal(str, object)
    model_load_failed = pyqtSignal(str)
    image_inspection_finished = pyqtSignal(str, object)
    inspection_finished = pyqtSignal(str, object)
    inspection_image_saved = pyqtSignal(str, str)
    inspection_image_save_failed = pyqtSignal(str, str)
    image_circle_redetection_finished = pyqtSignal(str, object)
    circle_redetection_finished = pyqtSignal(str, object)
    shutdown_complete = pyqtSignal()

    def __init__(
        self,
        segmentation_service,
        circle_model_service,
        circle_detector,
        rule_engine,
    ):
        super().__init__()
        self._segmentation_service = segmentation_service
        self._circle_model_service = circle_model_service
        self._circle_detector = circle_detector
        self._rule_engine = rule_engine

    @pyqtSlot(str, str, object)
    def load_model(self, model_path: str, circle_model_path: str, config):
        """在当前后台线程加载并预热分割模型和专用找圆模型。"""

        try:
            self._segmentation_service.load(
                model_path,
                imgsz=config.inference_imgsz,
                confidence_floor=config.inference_confidence_floor,
            )
            self._circle_model_service.load(
                circle_model_path,
                confidence_floor=config.circle.confidence_floor,
            )
            metadata = {
                "class_names": self._segmentation_service.class_names,
                "load_ms": self._segmentation_service.load_ms,
                "warmup_ms": self._segmentation_service.warmup_ms,
                "circle_model_path": self._circle_model_service.model_path,
                "circle_load_ms": self._circle_model_service.load_ms,
                "circle_warmup_ms": self._circle_model_service.warmup_ms,
            }
            self.model_loaded.emit(
                self._segmentation_service.model_path,
                metadata,
            )
        except Exception as error:
            self.model_load_failed.emit(_error_message(error))

    @pyqtSlot(str, object, object, object)
    def inspect(self, task_id: str, image, config, original_image_path=None):
        """对整图找圆，逐 ROI 分割并输出多圆结果及旧 GUI 兼容结果。"""

        image_result, compatibility_result = self._inspect_image(
            task_id,
            image,
            config,
        )
        self._save_result_image(task_id, image, image_result, config, original_image_path)
        # 先发送完整多圆结果；旧信号继续服务当前单圆 GUI。
        self.image_inspection_finished.emit(task_id, image_result)
        self.inspection_finished.emit(task_id, compatibility_result)

    def _save_result_image(self, task_id, image, image_result, config, original_image_path):
        if original_image_path:
            # 绘制和编码在检测线程完成，不受 GUI 缩放、选中圆或显示开关影响。
            # 保存失败只报告文件错误，仍正常发布本次检测结果。
            try:
                output_path = save_inspection_image(
                    image, image_result, config, original_image_path,
                )
            except Exception as error:
                logger.exception("检测结果图保存失败: task=%s", task_id)
                self.inspection_image_save_failed.emit(task_id, _error_message(error))
            else:
                self.inspection_image_saved.emit(task_id, output_path)
    @pyqtSlot(str, object, object, object, object)
    def redetect_circle(self, task_id: str, image, source_result, config, original_image_path=None):
        """重新找圆后按新 ROI 重跑分割，避免复用已经错位的局部结果。"""

        del source_result
        image_result, compatibility_result = self._inspect_image(
            task_id,
            image,
            config,
        )
        image_result.timings_ms["circle_redetection_total"] = (
            image_result.timings_ms.get("total", 0.0)
        )
        compatibility_result.timings_ms["circle_redetection_total"] = (
            compatibility_result.timings_ms.get("total", 0.0)
        )
        self._save_result_image(task_id, image, image_result, config, original_image_path)
        self.image_circle_redetection_finished.emit(task_id, image_result)
        self.circle_redetection_finished.emit(task_id, compatibility_result)

    def _inspect_image(self, task_id: str, image, config):
        """执行一次不访问 Qt 控件的多圆 ROI 检测编排。"""

        total_start = time.perf_counter()
        try:
            image_height, image_width = _image_size(image)
            expected_count = int(config.circle.expected_circle_count)
        except Exception as error:
            image_result = ImageInspectionResult(
                image_id=str(task_id),
                status=InspectionStatus.ERROR,
                error=_error_message(error),
                timings_ms={"total": _elapsed_ms(total_start)},
            )
            return image_result, _compatibility_result(
                image_result,
                [],
                None,
            )

        try:
            circle_start = time.perf_counter()
            (
                candidates,
                selected_index,
                detector_confirmed,
                circle_warnings,
            ) = self._circle_detector.detect(image, config.circle)
            circle_ms = _elapsed_ms(circle_start)
        except Exception as error:
            image_result = ImageInspectionResult(
                image_id=str(task_id),
                image_width=image_width,
                image_height=image_height,
                mm_per_pixel=float(config.mm_per_pixel),
                expected_circle_count=expected_count,
                status=InspectionStatus.ERROR,
                error=_error_message(error),
                timings_ms={"total": _elapsed_ms(total_start)},
            )
            return image_result, _compatibility_result(
                image_result,
                [],
                None,
            )

        image_result = ImageInspectionResult(
            image_id=str(task_id),
            image_width=image_width,
            image_height=image_height,
            mm_per_pixel=float(config.mm_per_pixel),
            expected_circle_count=expected_count,
            detected_circle_count=len(candidates),
            warnings=list(circle_warnings),
        )
        inference_total_ms = 0.0
        evaluation_total_ms = 0.0

        for index, candidate in enumerate(candidates, start=1):
            circle_id = f"circle-{index:03d}"
            circle_start = time.perf_counter()
            circle_result = CircleInspectionResult(
                circle_id=circle_id,
                circle_candidate=candidate,
                circle_confirmed=(
                    candidate.score >= config.circle.confidence_floor
                ),
            )
            try:
                crop_start = time.perf_counter()
                roi = build_circle_roi(
                    candidate,
                    circle_id=circle_id,
                    roi_size_px=config.roi_size_px,
                    image_width=image_width,
                    image_height=image_height,
                )
                circle_result.roi = roi
                roi_image = crop_roi(image, roi)
                circle_result.timings_ms["roi_crop"] = _elapsed_ms(crop_start)
                if is_roi_clipped(roi):
                    circle_result.warnings.append(
                        f"{circle_id} ROI 超出原图边界，已按有效范围裁切"
                    )

                inference_start = time.perf_counter()
                try:
                    local_instances = self._segmentation_service.predict(
                        roi_image,
                        imgsz=config.inference_imgsz,
                        confidence_floor=config.inference_confidence_floor,
                    )
                finally:
                    inference_ms = _elapsed_ms(inference_start)
                    inference_total_ms += inference_ms
                    circle_result.timings_ms["inference"] = inference_ms
                instances = restore_instances_to_image(local_instances, roi)

                evaluation_start = time.perf_counter()
                try:
                    evaluated = self._rule_engine.evaluate(
                        instances=instances,
                        circle_candidates=[candidate],
                        selected_circle_index=0,
                        circle_confirmed=circle_result.circle_confirmed,
                        mm_per_pixel=config.mm_per_pixel,
                        region_rules=config.region_rules,
                        image_width=image_width,
                        image_height=image_height,
                    )
                finally:
                    evaluation_ms = _elapsed_ms(evaluation_start)
                    evaluation_total_ms += evaluation_ms
                    circle_result.timings_ms["evaluation"] = evaluation_ms

                circle_result.status = evaluated.status
                if (
                    not circle_result.circle_confirmed
                    and circle_result.status == InspectionStatus.PASS
                ):
                    circle_result.status = InspectionStatus.PENDING
                circle_result.error = evaluated.error
                _extend_unique(circle_result.warnings, evaluated.warnings)
                circle_result.instances = list(evaluated.instances)
                circle_result.region_results = list(evaluated.region_results)
                circle_result.failure_reasons = list(
                    evaluated.failure_reasons
                )
                circle_result.completed = True
            except Exception as error:
                circle_result.status = InspectionStatus.ERROR
                circle_result.error = _error_message(error)
                circle_result.completed = True
            circle_result.timings_ms["total"] = _elapsed_ms(circle_start)
            image_result.circle_results.append(circle_result)

        for index in range(len(candidates) + 1, expected_count + 1):
            circle_id = f"circle-{index:03d}"
            image_result.circle_results.append(CircleInspectionResult(
                circle_id=circle_id,
                status=InspectionStatus.PENDING,
                completed=False,
                warnings=[f"{circle_id} 未找到对应候选圆"],
            ))

        _finish_image_result(
            image_result,
            detector_confirmed=detector_confirmed,
        )
        image_result.timings_ms.update({
            "circle_detection": circle_ms,
            "inference": inference_total_ms,
            "evaluation": evaluation_total_ms,
            "total": _elapsed_ms(total_start),
        })
        compatibility_result = _compatibility_result(
            image_result,
            candidates,
            selected_index,
        )
        return image_result, compatibility_result

    @pyqtSlot()
    def shutdown(self):
        """在 Worker 线程中释放模型引用，然后通知上层退出线程。"""

        try:
            self._segmentation_service.unload_for_shutdown()
            self._circle_model_service.unload_for_shutdown()
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


def _finish_image_result(
    result: ImageInspectionResult,
    *,
    detector_confirmed: bool,
):
    """汇总端面结果；明确失败优先于错误和待确认。"""

    result.completed_circle_count = sum(
        1 for item in result.circle_results if item.completed
    )
    expected_results = result.circle_results[:result.expected_circle_count]
    result.is_complete = bool(
        detector_confirmed
        and result.detected_circle_count == result.expected_circle_count
        and len(expected_results) == result.expected_circle_count
        and all(
            item.completed
            and item.circle_confirmed
            and item.status in (InspectionStatus.PASS, InspectionStatus.FAIL)
            for item in expected_results
        )
    )

    for item in result.circle_results:
        for warning in item.warnings:
            _append_unique(result.warnings, warning)
        for reason in item.failure_reasons:
            _append_unique(
                result.failure_reasons,
                f"{item.circle_id}: {reason}",
            )

    failed = any(
        item.status == InspectionStatus.FAIL
        for item in result.circle_results
    )
    error_results = [
        item
        for item in result.circle_results
        if item.status == InspectionStatus.ERROR
    ]
    errors = [
        f"{item.circle_id}: {item.error or '检测错误'}"
        for item in error_results
    ]
    result.error = "；".join(errors)
    if failed:
        result.status = InspectionStatus.FAIL
    elif error_results:
        result.status = InspectionStatus.ERROR
    elif result.is_complete:
        result.status = InspectionStatus.PASS
    else:
        result.status = InspectionStatus.PENDING


def _compatibility_result(
    image_result: ImageInspectionResult,
    candidates,
    selected_index,
) -> InspectionResult:
    """把多圆结果投影为当前单圆 GUI 可以继续展示的旧结构。"""

    selected_result = None
    if (
        selected_index is not None
        and 0 <= selected_index < len(candidates)
        and selected_index < len(image_result.circle_results)
    ):
        selected_result = image_result.circle_results[selected_index]

    expected_results = image_result.circle_results[
        :image_result.expected_circle_count
    ]
    all_expected_circles_confirmed = bool(
        image_result.detected_circle_count
        == image_result.expected_circle_count
        and len(expected_results) == image_result.expected_circle_count
        and all(item.circle_confirmed for item in expected_results)
    )

    return InspectionResult(
        status=image_result.status,
        error=image_result.error,
        warnings=list(image_result.warnings),
        image_width=image_result.image_width,
        image_height=image_result.image_height,
        mm_per_pixel=image_result.mm_per_pixel,
        circle_candidates=list(candidates),
        selected_circle_index=(
            selected_index if selected_result is not None else None
        ),
        circle_confirmed=(
            all_expected_circles_confirmed
            and selected_result.circle_confirmed
            if selected_result is not None
            else False
        ),
        instances=(
            list(selected_result.instances)
            if selected_result is not None
            else []
        ),
        region_results=(
            list(selected_result.region_results)
            if selected_result is not None
            else []
        ),
        failure_reasons=list(image_result.failure_reasons),
        timings_ms=dict(image_result.timings_ms),
    )


def _append_unique(target: list[str], value: str):
    if value and value not in target:
        target.append(value)


def _extend_unique(target: list[str], values):
    for value in values:
        _append_unique(target, value)
