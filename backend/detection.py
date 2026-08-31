import logging
import os
from typing import Optional, Tuple

from backend.camera_utils import (
    box_to_roi,
    fallback_roi,
)


logger = logging.getLogger(__name__)

_detect_model_cache = {}
_torch_threads_limited = False

def _get_detect_model(model_path: str):
    """模型单例：整个进程只加载一次，避免重复加载导致原生崩溃。"""
    global _torch_threads_limited
    if model_path not in _detect_model_cache:
        import torch
        if not _torch_threads_limited:
            torch.set_num_threads(4)      # 限制 PyTorch 线程数，避免与 Qt/相机线程冲突
            _torch_threads_limited = True
        from ultralytics import YOLO
        _detect_model_cache[model_path] = YOLO(model_path)
    return _detect_model_cache[model_path]
def detect_roi(
    image,
    model_path: str,
    conf: float,
    binning: int,
    fallback_size: int,
    model=None,
    sensor_size: Optional[Tuple[int, int]] = None,
) -> Tuple[Tuple[int, int, int, int], str, Optional[Tuple[float, float, float, float]]]:
    """YOLO 检测定 ROI；失败或模型缺失 → 降级居中固定 ROI。

    返回 (roi, 来源, 原始检测框)；原始框在降采样图像坐标系，无检测时返回 None。
    """
    if model is not None:
        try:
            results = model.predict(source=image, conf=conf, verbose=False)
            boxes = results[0].boxes
            if boxes is not None and len(boxes) > 0:
                x1, y1, x2, y2 = boxes.xyxy[0].tolist()
                return (
                    box_to_roi(
                        x1,
                        y1,
                        x2,
                        y2,
                        binning,
                        sensor_size=sensor_size,
                    ),
                    "detect",
                    (x1, y1, x2, y2),
                )
        except Exception as e:
            logger.warning(
                "检测失败（外部模型）: %s",
                e,
            )
    elif model_path and os.path.exists(model_path):
        try:
            from ultralytics import YOLO

            model = _get_detect_model(model_path)
            results = model.predict(source=image, conf=conf, verbose=False)
            boxes = results[0].boxes
            if boxes is not None and len(boxes) > 0:
                x1, y1, x2, y2 = boxes.xyxy[0].tolist()
                return (
                    box_to_roi(
                        x1,
                        y1,
                        x2,
                        y2,
                        binning,
                        sensor_size=sensor_size,
                    ),
                    "detect",
                    (x1, y1, x2, y2),
                )
        except Exception as e:
            logger.warning(
                "检测失败: %s",
                e,
            )
    return (
        fallback_roi(
            fallback_size,
            sensor_size=sensor_size,
        ),
        "fallback",
        None,
    )


def detect_local_roi(
    image,
    conf: float,
    model,
) -> Tuple[
    Tuple[int, int, int, int],
    str,
    Optional[Tuple[float, float, float, float]],
]:
    """在当前相机硬件ROI图像内检测清晰度评价框。

    与 :func:`detect_roi` 的区别是，本函数返回的坐标始终属于输入
    ``image``，不会乘binning，也不会转换成传感器全幅坐标。检测失败
    时使用整张输入图像，并明确返回 ``fallback_preset``。
    """

    if image is None or getattr(image, "ndim", 0) < 2:
        raise ValueError("YOLO局部ROI检测收到无效图像")

    height, width = image.shape[:2]
    fallback = (0, 0, int(width), int(height))

    if model is None:
        logger.warning("YOLO模型不可用，使用整个预设硬件ROI评价")
        return fallback, "fallback_preset", None

    try:
        results = model.predict(
            source=image,
            conf=float(conf),
            verbose=False,
        )
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            logger.warning("第一帧YOLO未检测到目标，使用预设ROI兜底")
            return fallback, "fallback_preset", None

        box_index = 0
        confidences = getattr(boxes, "conf", None)
        if confidences is not None and len(confidences) > 0:
            try:
                box_index = int(confidences.argmax().item())
            except Exception:
                box_index = 0

        x1, y1, x2, y2 = boxes.xyxy[box_index].tolist()
        raw_box = (
            float(x1),
            float(y1),
            float(x2),
            float(y2),
        )

        left = max(0, min(width - 1, int(round(x1))))
        top = max(0, min(height - 1, int(round(y1))))
        right = max(left + 1, min(width, int(round(x2))))
        bottom = max(top + 1, min(height, int(round(y2))))
        local_roi = (
            left,
            top,
            right - left,
            bottom - top,
        )
        return local_roi, "detect", raw_box

    except Exception as error:
        logger.warning(
            "第一帧YOLO检测异常，使用预设ROI兜底: %s",
            error,
        )
        return fallback, "fallback_preset", None
