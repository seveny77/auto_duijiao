# -*- coding: utf-8 -*-
"""AI 对焦模型后台加载工作对象。"""

import logging

from PyQt5.QtCore import QObject, pyqtSignal


logger = logging.getLogger(__name__)


class DLModelLoadWorker(QObject):
    """在后台线程中执行一次 AI 模型加载和预热。"""

    # 加载成功：
    # model      已加载并完成预热的模型对象
    # model_path 实际加载的绝对路径
    loaded = pyqtSignal(
        object,
        str,
    )

    # 加载失败时发送适合 GUI 展示的简短信息。
    failed = pyqtSignal(str)

    # 无论成功还是失败，run() 结束前都会发送。
    settled = pyqtSignal()

    def __init__(
            self,
            model_service,
            model_path: str,
    ):
        super().__init__()

        self._model_service = model_service
        self._model_path = model_path

    def run(self):
        """执行一次模型加载任务。"""

        try:
            model = self._model_service.load(
                self._model_path
            )

            if model is None:
                self.failed.emit(
                    "AI 对焦模型加载失败，"
                    "请查看日志了解详细原因"
                )
                return

            loaded_path = (
                self._model_service.model_path
                or ""
            )

            self.loaded.emit(
                model,
                loaded_path,
            )

        except Exception as exc:
            # DLModelService.load() 正常会捕获模型加载异常。
            #
            # 这里仍设置最后一道异常边界，防止 Worker 中其他代码
            # 出错后导致线程静默结束，GUI 永远停留在“加载中”。
            logger.exception(
                "AI 对焦模型 Worker 发生未处理异常"
            )

            self.failed.emit(
                f"AI 对焦模型加载异常: {exc}"
            )

        finally:
            self.settled.emit()
