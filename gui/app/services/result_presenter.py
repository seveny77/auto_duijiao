# -*- coding: utf-8 -*-
"""搜索和标定结果展示服务。"""

import logging
import time
import cv2


logger = logging.getLogger(__name__)


class ResultPresenter:
    """把后端结果转换成日志、曲线、图像和状态栏信息。"""

    def __init__(
        self,
        image_widget,
        curve_panel,
        ct_logger,
        controller,
        message_fn,
        status_fn,
        template_path_fn,
    ):
        self._image_widget = image_widget
        self._curve_panel = curve_panel
        self._ct_logger = ct_logger
        self._controller = controller
        self._message_fn = message_fn
        self._status_fn = status_fn
        self._template_path_fn = template_path_fn
        self._last_preview_ts = 0.0

    def begin_task(self):
        """新任务开始前清空旧结果并重置预览状态。"""

        self._curve_panel.clear_curve()
        self.reset_preview()

    def reset_preview(self):
        """新任务开始前重置过程预览的限频时间。"""
        self._last_preview_ts = 0.0

    def present_preview(
            self,
            image,
            phase: str,
            sequence: int,
            score: float,
    ):
        """展示标定、粗扫和精扫过程中的抽样帧。"""

        if image is None:
            return

        # GUI 侧的保护性限频。
        now = time.monotonic()

        if now - self._last_preview_ts < 0.05:
            return

        self._last_preview_ts = now

        phase_labels = {
            "calibrate": "标定",
            "coarse": "粗扫",
            "fine": "精扫",
        }

        phase_text = phase_labels.get(
            phase,
            phase,
        )

        # 不主动重置图像视图，
        # 所以新帧到达时能够保留用户的缩放和拖拽位置。
        self._image_widget.show_frame(image)

        # 后端帧序号从0开始，
        # 界面显示时加1，更符合用户习惯。
        self._status_fn(
            f"{phase_text}："
            f"第 {sequence + 1} 帧，"
            f"清晰度 {score:.1f}"
        )

    def handle_finished(self, result):
        """处理后台正常返回的搜索或标定结果。"""

        if result.rc != 0:
            error_message = str(result.error or "").strip() or "未知错误"

            if "取消" in error_message:
                self._controller.set_state(self._controller.STATE_DONE)
                self._message_fn("[已取消] 流程被用户停止")
                self._status_fn("流程已取消")
            else:
                self._controller.set_state(self._controller.STATE_ERROR)
                self._message_fn(f"[失败] {error_message}")
                self._status_fn("执行失败")

            return

        self._controller.set_state(self._controller.STATE_DONE)
        self.present(result)

    def handle_error(self, error_text: str):
        """处理后台任务未捕获的异常。"""

        message = str(error_text).strip() or "未知后台异常"

        if "取消" in message:
            self._controller.set_state(self._controller.STATE_DONE)
            self._message_fn("[已取消] 后台任务已停止")
            self._status_fn("流程已取消")
            return

        self._controller.set_state(self._controller.STATE_ERROR)
        self._message_fn(f"[错误] 后台任务异常: {message}")
        self._status_fn("后台任务异常")

    def present(self, result):
        """根据结果类型选择对应的展示方法。"""

        if result.action == "search":
            self._present_search(result)
        elif result.action == "calibrate":
            self._present_calibrate(result)
        else:
            logger.warning("无法展示未知动作的结果: %s", result.action)

    def load_template(self) -> bool:
        """加载模板文件，并展示模板基本信息。"""

        path = self._template_path_fn()

        if not path:
            logger.error("模板路径为空")
            return False

        try:
            from focus_template import FocusTemplate

            template = FocusTemplate.load(path)
        except FileNotFoundError:
            logger.error(
                "模板文件不存在: %s",
                path,
            )
            return False
        except Exception:
            logger.exception(
                "模板加载失败: %s",
                path,
            )
            return False

        logger.info(
            "模板加载成功: 峰位置=%s, FWHM=%.2f",
            template.peak_position,
            template.peak_width,
        )

        self._status_fn("模板已加载")
        return True

    def _present_search(self, result):
        """展示搜索对焦结果。"""

        self._message_fn(
            f"预测峰={result.predicted_peak_um}µm  "
            f"quality={result.quality}  "
            f"最终位置={result.final_position_um:g}µm"
        )
        self._ct_logger.log(result.ct_ms)

        self._curve_panel.plot_points(
            result.coarse_points,
            "粗扫",
            "#1f77b4",
        )
        self._curve_panel.plot_points(
            result.fine_points,
            "精扫",
            "#ff7f0e",
        )
        self._curve_panel.plot_peak(result.predicted_peak_um)

        if result.final_image is not None and result.roi is not None:
            self._show_final_image(result)
        elif result.fine_best_image is not None:
            self._show_image(
                result.fine_best_image,
                f"精扫最佳帧 index={result.fine_best}",
            )

        self._status_fn("搜索完成，已保持在最终清晰位置")

    def _show_final_image(self, result):
        """在定拍全幅图上绘制 ROI 后显示。"""

        x, y, w, h = result.roi

        # 使用副本进行标注，避免直接修改后端返回的原始图像。
        annotated = result.final_image.copy()

        cv2.rectangle(
            annotated,
            (x, y),
            (x + w, y + h),
            (0, 255, 0),
            3,
        )
        cv2.putText(
            annotated,
            f"ROI {w}x{h} ({result.roi_src})",
            (x, max(y - 12, 24)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (0, 255, 0),
            2,
        )

        self._show_image(
            annotated,
            "定拍全幅帧（ROI 已标注）",
        )

    def _present_calibrate(self, result):
        """展示图像标定结果。"""

        path = self._template_path_fn()

        self._message_fn(f"标定完成: 模板已保存到 {path}")
        self._message_fn(
            f"峰 index={result.peak_position}, "
            f"FWHM={result.peak_width:.2f}, "
            f"峰位置={result.peak_um}µm"
        )
        self._ct_logger.log(result.ct_ms)

        try:
            self._plot_template_curve(path)
        except Exception:
            logger.exception("标定曲线绘制失败: %s", path)

        self._status_fn("标定完成，轴保持在标定结束位置")

    def _plot_template_curve(self, path: str):
        """从保存后的模板文件中恢复并绘制标定曲线。"""

        from focus_template import FocusTemplate

        template = FocusTemplate.load(path)
        start = template.meta.get("start_um", 0)
        step = template.meta.get("step_um", 1)

        points = [
            (start + (index + 1) * step, score)
            for index, score in enumerate(template.curve)
        ]

        self._curve_panel.plot_points(
            points,
            "标定曲线",
            "#2ca02c",
        )
        self._curve_panel.plot_peak(
            start + (template.peak_position + 1) * step,
            "模板峰",
        )

    def _show_image(self, image, title: str):
        """显示一张结果图，并记录对应的界面提示。"""

        self._image_widget.show_frame(image)
        self._message_fn(f"图像区显示: {title}")
