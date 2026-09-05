# -*- coding: utf-8 -*-
"""端面圆 YOLO 检测模型的延迟加载、预热和候选输出。"""

import logging
import math
import os
import time
from typing import Callable, Optional

from backend.inspection_types import CircleCandidate
from backend.ultralytics_runtime import (
    device_predict_kwargs,
    load_yolo_class,
    resolve_yolo_device,
)


# 找圆只需要端面的大致位置；固定最长边可避免大图直接进入 YOLO。
INFERENCE_LONGEST_SIDE = 1024
logger = logging.getLogger(__name__)


class CircleModelService:
    """持有一个普通 YOLO Detect 圆定位模型，运行期间不允许切换。"""

    def __init__(self, model_factory: Optional[Callable] = None):
        self._model_factory = model_factory
        self._model = None
        self._model_path = ""
        self._load_ms = 0.0
        self._warmup_ms = 0.0
        self._device = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_path(self) -> str:
        return self._model_path

    @property
    def load_ms(self) -> float:
        return self._load_ms

    @property
    def warmup_ms(self) -> float:
        return self._warmup_ms

    @property
    def device(self) -> Optional[str]:
        return self._device

    def load(self, model_path: str, *, confidence_floor: float):
        """在后台线程加载并预热普通 YOLO 检测模型。"""

        path = os.path.abspath(str(model_path).strip())
        if not str(model_path).strip():
            raise ValueError("找圆模型路径不能为空")
        if not path.lower().endswith(".pt"):
            raise ValueError("找圆模型必须是 .pt 文件")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"找圆模型文件不存在: {path}")
        _validate_confidence(confidence_floor)

        if self._model is not None:
            if os.path.normcase(path) == os.path.normcase(self._model_path):
                return self._model
            raise RuntimeError("找圆模型已经加载，重启软件后才能更换模型")

        factory = self._model_factory
        if factory is None:
            # 仅在用户明确加载模型时导入，避免启动软件时初始化 Torch/CUDA。
            factory = load_yolo_class()

        device = resolve_yolo_device()
        if device == "cpu":
            logger.warning(
                "找圆推理已显式配置为 CPU；"
                "设置 AUTOFOCUS_YOLO_DEVICE=0 可显式启用首张 CUDA 设备"
            )

        load_start = time.perf_counter()
        candidate = factory(path)
        load_ms = (time.perf_counter() - load_start) * 1000.0
        if str(getattr(candidate, "task", "")).lower() != "detect":
            raise ValueError("所选找圆模型不是普通 YOLO Detect 模型")

        import numpy as np

        warmup_image = np.zeros((INFERENCE_LONGEST_SIDE, INFERENCE_LONGEST_SIDE, 3), dtype=np.uint8)
        warmup_start = time.perf_counter()
        candidate.predict(
            source=warmup_image,
            imgsz=INFERENCE_LONGEST_SIDE,
            conf=float(confidence_floor),
            max_det=20,
            verbose=False,
            **device_predict_kwargs(device),
        )
        warmup_ms = (time.perf_counter() - warmup_start) * 1000.0

        self._model = candidate
        self._model_path = path
        self._load_ms = load_ms
        self._warmup_ms = warmup_ms
        self._device = device
        return self._model

    def predict_circles(
        self,
        image,
        *,
        expected_count: int,
        confidence_floor: float,
    ) -> tuple[list[CircleCandidate], Optional[int], bool, list[str]]:
        """推理并按置信度选前 N 个，再按位置赋予稳定顺序。"""

        if self._model is None:
            raise RuntimeError("找圆模型尚未加载")
        if expected_count < 1:
            raise ValueError("预期圆数量必须至少为 1")
        _validate_confidence(confidence_floor)

        import cv2
        import numpy as np

        array = np.asarray(image)
        if array.ndim not in (2, 3) or array.size == 0:
            raise ValueError("找圆模型收到无效图像")
        if array.ndim == 2:
            array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
        elif array.ndim == 3 and array.shape[2] == 1:
            array = cv2.cvtColor(array[:, :, 0], cv2.COLOR_GRAY2BGR)
        elif array.ndim != 3 or array.shape[2] not in (3, 4):
            raise ValueError("找圆模型只支持灰度图、BGR 图或 BGRA 图")
        height, width = array.shape[:2]
        scale = min(1.0, INFERENCE_LONGEST_SIDE / max(width, height))
        if scale < 1.0:
            resized = cv2.resize(
                array,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            resized = array

        results = self._model.predict(
            source=resized,
            imgsz=INFERENCE_LONGEST_SIDE,
            conf=float(confidence_floor),
            max_det=max(20, expected_count),
            verbose=False,
            **device_predict_kwargs(self._device),
        )
        if results is None or len(results) != 1:
            raise RuntimeError("找圆模型必须为单张输入返回一个结果")

        boxes = getattr(results[0], "boxes", None)
        if boxes is None or len(boxes) == 0:
            return [], None, False, ["深度学习找圆未找到候选圆"]

        xyxy_values = _to_list(getattr(boxes, "xyxy", None))
        confidence_values = _to_list(getattr(boxes, "conf", None))
        class_values = _to_list(getattr(boxes, "cls", None))
        if not (len(xyxy_values) == len(confidence_values) == len(class_values)):
            raise RuntimeError("找圆模型输出框字段数量不一致")

        candidates = []
        for box, confidence, class_id in zip(xyxy_values, confidence_values, class_values):
            if int(class_id) != 0 or len(box) != 4:
                # 当前训练集只有 autofocus/0 类。忽略其他类以防选错模型。
                continue
            x1, y1, x2, y2 = (float(value) / scale for value in box)
            box_width = max(0.0, x2 - x1)
            box_height = max(0.0, y2 - y1)
            if box_width <= 0 or box_height <= 0:
                continue
            candidates.append(CircleCandidate(
                center_x=(x1 + x2) * 0.5,
                center_y=(y1 + y2) * 0.5,
                radius_px=(box_width + box_height) * 0.25,
                score=float(confidence),
                source="yolo",
            ))

        candidates.sort(key=lambda item: item.score, reverse=True)
        detected_count = len(candidates)
        selected = candidates[:expected_count]
        warnings: list[str] = []
        if detected_count != expected_count:
            warnings.append(
                f"预期检测到 {expected_count} 个圆，"
                f"深度学习找圆得到 {detected_count} 个候选圆"
            )
        if not selected:
            return [], None, False, warnings or ["深度学习找圆未找到有效候选圆"]

        highest_score = selected[0]
        selected.sort(key=lambda item: (item.center_x, item.center_y, item.radius_px))
        selected_index = next(index for index, item in enumerate(selected) if item is highest_score)
        confirmed = len(selected) == expected_count
        return selected, selected_index, confirmed, warnings

    def unload_for_shutdown(self):
        self._model = None
        self._model_path = ""
        self._device = None


def _validate_confidence(value: float):
    if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
        raise ValueError("找圆置信度下限必须在 0～1 之间")


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
