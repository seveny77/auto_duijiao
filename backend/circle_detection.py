# -*- coding: utf-8 -*-
"""通过专用 YOLO Detect 模型定位最终成像中的端面圆。"""

from backend.inspection_config import CircleDetectionConfig


class YoloCircleDetector:
    """找圆适配器：候选生成和预热均由 CircleModelService 负责。"""

    def __init__(self, model_service):
        self._model_service = model_service

    def detect(self, image, config: CircleDetectionConfig):
        return self._model_service.predict_circles(
            image,
            expected_count=int(config.expected_circle_count),
            confidence_floor=float(config.confidence_floor),
        )
