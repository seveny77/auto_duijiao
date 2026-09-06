"""连续自动对焦结果的图像、状态和日志呈现。"""

import time


class ResultPresenter:
    def __init__(self, image_widget, ct_logger, controller, message_fn, status_fn):
        self._image_widget = image_widget
        self._ct_logger = ct_logger
        self._controller = controller
        self._message_fn = message_fn
        self._status_fn = status_fn
        self._last_preview_ts = 0.0

    def begin_task(self):
        self._last_preview_ts = 0.0

    def present_preview(self, image, phase, sequence, score):
        if image is None:
            return
        now = time.monotonic()
        if now - self._last_preview_ts < 0.05:
            return
        self._last_preview_ts = now
        self._image_widget.show_frame(image)
        self._status_fn(f"连续采集：第 {sequence + 1} 帧，清晰度 {score:.1f}")

    def present_best_frame_ready(self, event):
        image = getattr(event, "image", None)
        if image is not None:
            self._image_widget.show_frame(image)
        focus_ms = getattr(event, "focus_ct_ms", {}).get("focus_total_ms", 0.0)
        self._message_fn(
            f"连续精扫最佳帧已确定：index={getattr(event, 'best_index', -1)}，"
            f"清晰度={getattr(event, 'best_score', 0.0):.3f}，对焦CT={focus_ms:.1f}ms；轴正在回起点"
        )
        self._status_fn("最佳帧已确定，轴正在回扫描起点")

    def complete_continuous_return(self, result):
        if result.rc != 0:
            self.handle_finished(result)
            return
        self._controller.set_state(self._controller.STATE_DONE)
        self._ct_logger.log(result.ct_ms)
        self._message_fn(
            f"连续精扫完成：最佳帧 index={result.best_frame_index}，"
            f"已回到起始位置 {result.final_position_um:g}µm"
        )
        self._status_fn("连续精扫完成，已回到扫描起点")

    def handle_finished(self, result):
        if result.rc != 0:
            message = str(result.error or "未知错误")
            self._controller.set_state(
                self._controller.STATE_DONE if "取消" in message else self._controller.STATE_ERROR
            )
            self._message_fn("[已取消] 流程被用户停止" if "取消" in message else f"[失败] {message}")
            self._status_fn("流程已取消" if "取消" in message else "执行失败")
            return
        self.complete_continuous_return(result)

    def handle_error(self, error_text):
        message = str(error_text).strip() or "未知后台异常"
        self._controller.set_state(
            self._controller.STATE_DONE if "取消" in message else self._controller.STATE_ERROR
        )
        self._message_fn(f"[错误] 后台任务异常: {message}")
        self._status_fn("流程已取消" if "取消" in message else "后台任务异常")
