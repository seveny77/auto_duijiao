# -*- coding: utf-8 -*-
"""单模型 YOLO-Seg 延迟加载和分割实例标准化服务。"""

import logging
import math
import os
import time
from typing import Callable, Optional

from backend.inspection_types import SegmentationInstance


logger = logging.getLogger(__name__)


class SegmentationModelService:
    """持有一个分割模型；成功加载后本次进程不允许切换。"""

    def __init__(self, model_factory: Optional[Callable] = None):
        self._model_factory = model_factory
        self._model = None
        self._model_path = ""
        self._class_names: dict[int, str] = {}
        self._load_ms = 0.0
        self._warmup_ms = 0.0

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_path(self) -> str:
        return self._model_path

    @property
    def class_names(self) -> dict[int, str]:
        return dict(self._class_names)

    @property
    def load_ms(self) -> float:
        return self._load_ms

    @property
    def warmup_ms(self) -> float:
        return self._warmup_ms

    def load(
        self,
        model_path: str,
        *,
        imgsz: int = 1280,
        confidence_floor: float = 0.01,
    ):
        """加载并预热唯一模型；失败时服务仍保持未加载状态。"""

        path = os.path.abspath(str(model_path).strip())
        if not str(model_path).strip():
            raise ValueError("分割模型路径不能为空")
        if not path.lower().endswith(".pt"):
            raise ValueError("分割模型必须是 .pt 文件")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"分割模型文件不存在: {path}")
        _validate_inference_options(imgsz, confidence_floor)

        if self._model is not None:
            if os.path.normcase(path) == os.path.normcase(self._model_path):
                return self._model
            raise RuntimeError("检测模型已经加载，重启软件后才能更换模型")

        factory = self._model_factory
        if factory is None:
            # 只有用户明确加载模型时才导入 Ultralytics/Torch。
            from ultralytics import YOLO
            factory = YOLO

        load_start = time.perf_counter()
        candidate = factory(path)
        load_ms = (time.perf_counter() - load_start) * 1000.0

        if str(getattr(candidate, "task", "")).lower() != "segment":
            raise ValueError("所选模型不是 YOLO-Seg 分割模型")

        class_names = _normalize_class_names(getattr(candidate, "names", None))

        import numpy as np
        warmup_image = np.zeros((640, 640, 3), dtype=np.uint8)
        warmup_start = time.perf_counter()
        candidate.predict(
            source=warmup_image,
            imgsz=int(imgsz),
            conf=float(confidence_floor),
            retina_masks=True,
            verbose=False,
        )
        warmup_ms = (time.perf_counter() - warmup_start) * 1000.0

        # 所有检查和预热成功后才正式持有，失败不会污染服务状态。
        self._model = candidate
        self._model_path = path
        self._class_names = class_names
        self._load_ms = load_ms
        self._warmup_ms = warmup_ms
        return self._model

    def predict(
        self,
        image,
        *,
        imgsz: int = 1280,
        confidence_floor: float = 0.01,
    ) -> list[SegmentationInstance]:
        """对单张图像或 ROI 推理并返回普通 Python 分割实例。"""

        if self._model is None:
            raise RuntimeError("分割模型尚未加载")
        if image is None or getattr(image, "ndim", 0) not in (2, 3):
            raise ValueError("分割推理收到无效图像")
        if getattr(image, "size", 0) == 0:
            raise ValueError("分割推理收到空图像")
        _validate_inference_options(imgsz, confidence_floor)

        results = self._model.predict(
            source=image,
            imgsz=int(imgsz),
            conf=float(confidence_floor),
            retina_masks=True,
            verbose=False,
        )
        if results is None or len(results) != 1:
            raise RuntimeError("分割模型必须为单张输入返回一个结果")
        return _convert_result(results[0], self._class_names)

    def unload_for_shutdown(self):
        """仅供应用关闭时释放引用，不支持运行中切换模型。"""

        self._model = None
        self._model_path = ""
        self._class_names = {}


def _validate_inference_options(imgsz: int, confidence_floor: float):
    if int(imgsz) < 1:
        raise ValueError("分割推理尺寸 imgsz 必须大于 0")
    if not math.isfinite(float(confidence_floor)) or not (
        0 <= float(confidence_floor) <= 1
    ):
        raise ValueError("分割推理置信度下限必须在 0～1 之间")


def _normalize_class_names(names) -> dict[int, str]:
    if isinstance(names, dict):
        normalized = {int(key): str(value) for key, value in names.items()}
    elif isinstance(names, (list, tuple)):
        normalized = {index: str(value) for index, value in enumerate(names)}
    else:
        raise ValueError("分割模型没有有效的类别名称 names")
    if not normalized or any(not value.strip() for value in normalized.values()):
        raise ValueError("分割模型类别名称不能为空")
    return normalized


def _convert_result(result, class_names) -> list[SegmentationInstance]:
    boxes = getattr(result, "boxes", None)
    box_count = 0 if boxes is None else len(boxes)
    masks = getattr(result, "masks", None)

    if box_count == 0:
        return []
    if masks is None:
        raise RuntimeError("模型返回了检测框，但没有分割 masks")

    polygons = getattr(masks, "xy", None)
    if polygons is None or len(polygons) != box_count:
        raise RuntimeError("分割 boxes 与 masks 数量不一致")

    classes = _to_list(getattr(boxes, "cls", None))
    confidences = _to_list(getattr(boxes, "conf", None))
    bounding_boxes = _to_list(getattr(boxes, "xyxy", None))
    if not (
        len(classes) == box_count
        and len(confidences) == box_count
        and len(bounding_boxes) == box_count
    ):
        raise RuntimeError("分割 boxes 字段数量不一致")

    instances = []
    for index in range(box_count):
        class_id = int(classes[index])
        if class_id not in class_names:
            raise RuntimeError(f"模型类别 {class_id} 不存在于 names")

        polygon_values = _to_list(polygons[index])
        polygon = []
        for point in polygon_values:
            values = _to_list(point)
            if len(values) != 2:
                raise RuntimeError(f"分割实例[{index}] polygon 点格式无效")
            x_value, y_value = float(values[0]), float(values[1])
            if not math.isfinite(x_value) or not math.isfinite(y_value):
                raise RuntimeError(f"分割实例[{index}] polygon 包含无效坐标")
            polygon.append((x_value, y_value))
        if len(polygon) < 3:
            logger.warning("分割实例[%d] polygon 少于3点，已忽略", index)
            continue

        bbox_values = _to_list(bounding_boxes[index])
        if len(bbox_values) != 4:
            raise RuntimeError(f"分割实例[{index}] bbox 格式无效")
        bbox = tuple(float(value) for value in bbox_values)

        instances.append(SegmentationInstance(
            class_id=class_id,
            class_name=class_names[class_id],
            confidence=float(confidences[index]),
            polygon=polygon,
            bbox=bbox,
            pixel_area=max(0, int(round(_polygon_area(polygon)))),
        ))
    return instances


def _to_list(value):
    if value is None:
        return []
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _polygon_area(polygon: list[tuple[float, float]]) -> float:
    twice_area = 0.0
    for current, following in zip(polygon, polygon[1:] + polygon[:1]):
        twice_area += (
            current[0] * following[1]
            - following[0] * current[1]
        )
    return abs(twice_area) * 0.5
