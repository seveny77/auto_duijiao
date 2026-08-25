# -*- coding: utf-8 -*-
"""YOLO检测模型加载与持有服务。"""

import logging
import os

from backend.config import FocusConfig


logger = logging.getLogger(__name__)


class DetectionModelService:
    """负责加载并持有搜索流程共用的YOLO模型。"""

    def __init__(
            self,
            model_path: str = None,
            project_root: str = None,
    ):
        default_path = FocusConfig().detect_model
        self._model_path = model_path or default_path

        if (
                self._model_path
                and project_root
                and not os.path.isabs(self._model_path)
        ):
            self._model_path = os.path.join(
                project_root,
                self._model_path,
            )

        self._model = None

    @property
    def model(self):
        """返回已经加载的模型；加载失败时返回None。"""

        return self._model

    @property
    def is_loaded(self) -> bool:
        """模型当前是否已经成功加载。"""

        return self._model is not None

    def load(self):
        """加载并预热 YOLO 模型，返回模型对象或 None。"""

        # 已经加载过就直接返回，避免重复创建模型。
        if self._model is not None:
            return self._model

        if not self._model_path:
            logger.warning("YOLO模型路径为空")
            return None

        if not os.path.exists(self._model_path):
            logger.warning(
                "YOLO模型不存在: %s",
                self._model_path,
            )
            return None

        try:
            import time
            import numpy as np
            import torch
            from ultralytics import YOLO

            # 限制 PyTorch CPU 线程数量，
            # 避免与 Qt、相机回调和图像评价线程抢占全部 CPU。
            torch.set_num_threads(4)

            load_start = time.perf_counter()

            self._model = YOLO(
                self._model_path
            )

            load_ms = (
                              time.perf_counter()
                              - load_start
                      ) * 1000

            logger.info(
                "YOLO模型已加载: %s，耗时 %.0fms",
                self._model_path,
                load_ms,
            )

            # 使用空白图完成第一次推理预热。
            #
            # 这里使用 640×640，不依赖相机物理分辨率。
            # YOLO 推理本身会把输入图像缩放到网络输入尺寸，
            # 所以不需要写死 5472×3648 或 1368×912。
            warmup_image = np.zeros(
                (640, 640, 3),
                dtype=np.uint8,
            )

            warmup_start = time.perf_counter()

            self._model.predict(
                source=warmup_image,
                conf=0.5,
                verbose=False,
            )

            warmup_ms = (
                                time.perf_counter()
                                - warmup_start
                        ) * 1000

            logger.info(
                "YOLO模型预热完成: %.0fms",
                warmup_ms,
            )

        except Exception:
            logger.exception(
                "YOLO模型加载或预热失败: %s",
                self._model_path,
            )

            self._model = None
            return None

        return self._model
