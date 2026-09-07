# -*- coding: utf-8 -*-
"""正式自动对焦质检结果的异步 Excel 记录服务。"""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
import threading

from PyQt5.QtCore import QObject, pyqtSignal

from backend.inspection_excel_recorder import InspectionExcelRecorder


class InspectionRecordService(QObject):
    """在单独线程串行写 Excel，不占用检测模型线程和 GUI 主线程。"""

    record_saved = pyqtSignal(str, str)
    record_failed = pyqtSignal(str, str)

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self._project_root = os.path.abspath(project_root)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="inspection-excel",
        )
        self._lock = threading.Lock()
        self._futures = set()
        self._closed = False

    def submit(
        self,
        task_id: str,
        result,
        config,
        *,
        original_image_path=None,
    ) -> bool:
        """把结果放入记录队列；关闭或配置禁用时返回 False。"""

        if not bool(getattr(config, "excel_record_enabled", True)):
            return False
        root = str(
            getattr(config, "excel_record_root", "inspection_records") or ""
        ).strip()
        if not root:
            self.record_failed.emit(str(task_id), "Excel 检测记录目录不能为空")
            return False
        if not os.path.isabs(root):
            root = os.path.join(self._project_root, root)

        with self._lock:
            if self._closed:
                return False
            result_snapshot = copy.deepcopy(result)
            future = self._executor.submit(
                _append_record,
                root,
                result_snapshot,
                str(original_image_path or ""),
                datetime.now(),
            )
            self._futures.add(future)
        future.add_done_callback(
            lambda completed, current_task_id=str(task_id):
            self._on_completed(current_task_id, completed)
        )
        return True

    def shutdown(self):
        """停止接收新任务，并等待已经排队的记录完整落盘。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _on_completed(self, task_id: str, future):
        with self._lock:
            self._futures.discard(future)
        try:
            output_path = future.result()
        except Exception as error:
            message = str(error).strip() or type(error).__name__
            self.record_failed.emit(
                task_id,
                f"{type(error).__name__}: {message}",
            )
        else:
            self.record_saved.emit(task_id, output_path)


def _append_record(
    root: str,
    result,
    original_image_path: str,
    completed_at: datetime,
) -> str:
    return InspectionExcelRecorder(root).append(
        result,
        completed_at=completed_at,
        original_image_path=original_image_path,
    )
