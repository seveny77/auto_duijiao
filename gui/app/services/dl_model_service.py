# -*- coding: utf-8 -*-
"""AI 单帧对焦模型的加载与持有服务。"""

import logging
import os
import time


logger = logging.getLogger(__name__)


class DLModelService:
    """负责加载、预热并持有 AI 单帧对焦模型。"""

    def __init__(
            self,
            project_root: str = None,
    ):
        self._project_root = project_root
        self._model = None
        self._model_path = None

    @property
    def model(self):
        """返回已经加载的模型；未加载时返回 None。"""

        return self._model

    @property
    def model_path(self):
        """返回当前已加载模型的绝对路径。"""

        return self._model_path

    @property
    def is_loaded(self) -> bool:
        """当前是否持有可用的 AI 对焦模型。"""

        return self._model is not None

    def resolve_path(
            self,
            model_path: str,
    ) -> str:
        """把模型路径转换成以项目根目录为基准的绝对路径。"""

        path = (model_path or "").strip()

        if not path:
            return ""

        if (
                self._project_root
                and not os.path.isabs(path)
        ):
            path = os.path.join(
                self._project_root,
                path,
            )

        return os.path.abspath(path)

    def is_current_model(
            self,
            model_path: str,
    ) -> bool:
        """指定路径是否就是当前已经加载的模型。"""

        if not self.is_loaded:
            return False

        resolved_path = self.resolve_path(
            model_path
        )

        return (
            os.path.normcase(resolved_path)
            == os.path.normcase(self._model_path)
        )

    def load(
            self,
            model_path: str,
    ):
        """加载并预热模型，成功时返回模型，失败时返回 None。"""

        resolved_path = self.resolve_path(
            model_path
        )

        if not resolved_path:
            logger.error("AI 对焦模型路径为空")
            return None

        if not os.path.isfile(resolved_path):
            logger.error(
                "AI 对焦模型不存在: %s",
                resolved_path,
            )
            return None

        if self.is_current_model(resolved_path):
            logger.info(
                "AI 对焦模型已经加载，无需重复加载: %s",
                resolved_path,
            )
            return self._model

        try:
            # 延迟导入 PyTorch 模型模块。
            #
            # GUI 启动且用户只使用 NCC 时，
            # 不需要因为导入本服务而立即初始化 PyTorch。
            from backend.dl_focus_model import (
                DLDistanceModel,
            )

            load_start = time.perf_counter()

            new_model = DLDistanceModel(
                model_path=resolved_path,
            )

            load_ms = (
                time.perf_counter()
                - load_start
            ) * 1000.0

            logger.info(
                "AI 对焦模型已加载: %s，耗时 %.0fms",
                resolved_path,
                load_ms,
            )

            warmup_start = time.perf_counter()

            warmup_delta_um = new_model.warmup()

            warmup_ms = (
                time.perf_counter()
                - warmup_start
            ) * 1000.0

            logger.info(
                "AI 对焦模型预热完成: "
                "输出 %.2f μm，耗时 %.0fms",
                warmup_delta_um,
                warmup_ms,
            )

        except Exception:
            logger.exception(
                "AI 对焦模型加载或预热失败: %s",
                resolved_path,
            )
            return None

        # 只有加载和预热全部成功后，
        # 才替换当前服务持有的模型。
        self._model = new_model
        self._model_path = resolved_path

        return self._model

    def clear(self):
        """清除当前持有的模型引用。"""

        self._model = None
        self._model_path = None
