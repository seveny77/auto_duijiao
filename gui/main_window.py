# -*- coding: utf-8 -*-
"""主窗口模块（课程 A：代码修缮）"""
import copy
import logging
import time
from gui.app.services.qt_log_handler import (
    install_qt_log_handler,
    remove_qt_log_handler,
)
import os
from gui.app.widgets.image_view import ImageWidget
from gui.app.widgets.curve_panel import CurvePanel
from gui.app.widgets.log_panel import LogPanel
from gui.app.widgets.inspection_panel import InspectionPanel
from gui.app.services.config_service import ConfigService
from backend.inspection_config import InspectionConfig, InspectionConfigStore
from backend.inspection_engine import InspectionRuleEngine
from gui.app.services.controller import AppController
from gui.app.services.ct_logger import CtLogger
from gui.app.services.result_presenter import ResultPresenter
from gui.app.services.motion_service import MotionService
from gui.app.services.inspection_service import InspectionService
from gui.app.services.inspection_image_loader import (
    load_inspection_image,
)
from gui.app.services.camera_service import CameraService
from gui.app.services.live_view_service import LiveViewService
from gui.app.services.focus_task_service import FocusTaskService
from gui.app.services.focus_run_service import FocusRunService
from gui.app.services.detection_model_service import DetectionModelService
from gui.app.services.application_shutdown_service import (
    ApplicationShutdownService,
)
from PyQt5.QtCore import (
    Qt,
    pyqtSignal,
)
from gui.app.widgets.param_panels import ParamPanel
from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QMessageBox,

)
# 创建当前模块的 logger
logger = logging.getLogger(__name__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # gui/ 的上级 = 项目根


def _resolve_path(path: str) -> str:
    """相对路径 → 以项目根为基准的绝对路径"""
    if path and not os.path.isabs(path):
        return os.path.join(PROJECT_ROOT, path)
    return path

class MainWindow(QMainWindow):
    """自动对焦系统主窗口。"""
    status_message = pyqtSignal(str)  # ★ 自定义信号：更新状态栏

    def __init__(self):
        super().__init__()
        self.setWindowTitle("端面检测系统")
        self.resize(1280, 800)
        self.statusBar().showMessage("就绪")
        self._build_ui()          # 把界面搭建交给单独的方法
        # 因此现在可以把标准 logging 接入 GUI。
        self._qt_log_handler = (
            install_qt_log_handler(
                append_fn=self._log,
                level=logging.INFO,
            )
        )
        logger.info(
            "标准日志系统已连接到 GUI"
        )
        self.controller = AppController(
            widgets_to_lock=self.param_panel.lock_widgets(),
            start_btn=self.param_panel.start_btn,
            stop_btn=self.param_panel.stop_btn,
            live_btn=self.image_widget.live_btn,
            status_fn=self.status_message.emit,
        )

        self.ct_logger = CtLogger()

        self.result_presenter = ResultPresenter(
            image_widget=self.image_widget,
            curve_panel=self.curve_panel,
            ct_logger=self.ct_logger,
            controller=self.controller,
            message_fn=self._log,
            status_fn=self.status_message.emit,
            template_path_fn=lambda: _resolve_path(
                self.param_panel.template_edit.text().strip()
            ),
        )
        self.focus_task_service = FocusTaskService()

        self.detection_model_service = DetectionModelService(
            project_root=PROJECT_ROOT,
        )
        self.detection_model_service.load()

        self.live_view_service = LiveViewService(
            project_root=PROJECT_ROOT,
            image_widget=self.image_widget,
            live_btn=self.image_widget.live_btn,
            status_fn=self.status_message.emit,
            camera_fn=lambda: self.camera_service.camera,
        )

        self.config_service = ConfigService(
            path=self._config_path(),
            panel=self.param_panel,
            project_root=PROJECT_ROOT,
            image_widget=self.image_widget,
        )
        self.config_service.load()

        # 检测配置独立保存，不混入现有对焦 config.json。
        self.inspection_config_store = InspectionConfigStore(
            os.path.join(PROJECT_ROOT, "gui", "inspection_config.json")
        )
        try:
            self.inspection_config = self.inspection_config_store.load()
        except (OSError, ValueError) as error:
            logger.warning("检测配置读取失败，将使用默认配置: %s", error)
            self.inspection_config = InspectionConfig()

        self.inspection_service = InspectionService(parent=self)
        self.inspection_recheck_engine = InspectionRuleEngine()
        self.inspection_panel.set_inspection_config(self.inspection_config)
        self._inspection_current_task_id = ""
        self._inspection_current_image = None
        self._inspection_image_result = None
        self._inspection_source_result = None
        self._inspection_circle_request_config = None
        self._inspection_close_requested = False
        self._inspection_shutdown_connected = False
        # 连续精扫的最佳图可能先于轴回位完成到达 GUI。
        self._continuous_best_frame_presented = False
        self.motion_service = MotionService(
            connect_btn=self.param_panel.motion_connect_btn,
            stroke_label=self.param_panel.motion_stroke_label,
            status_fn=self.status_message.emit,
            reset_btn=self.param_panel.motion_reset_btn,
            servo_btn=self.param_panel.motion_servo_btn,
            home_btn=self.param_panel.motion_home_btn,
            stop_btn=self.param_panel.motion_stop_btn,
            connection_label=self.param_panel.motion_connection_label,
            servo_label=self.param_panel.motion_servo_label,
            home_label=self.param_panel.motion_home_label,
            axis_label=self.param_panel.motion_axis_label,
            position_label=self.param_panel.motion_position_label,
        )
        self.camera_service = CameraService(
            connect_btn=self.param_panel.camera_connect_btn,
            status_fn=self.status_message.emit,
            connection_label=self.param_panel.camera_connection_label,
            roi_status_label=self.param_panel.camera_roi_status_label,
        )
        self.focus_run_service = FocusRunService(
            config_service=self.config_service,
            controller=self.controller,
            focus_task_service=self.focus_task_service,
            live_view_service=self.live_view_service,
            result_presenter=self.result_presenter,
            detection_model_service=self.detection_model_service,
            stroke_range_fn=lambda: self.motion_service.stroke_range,
            motion_backend_fn=lambda: self.motion_service.backend,
            motion_state_fn=lambda: self.motion_service.state,
            camera_fn=lambda: self.camera_service.camera,
            camera_roi_applied_fn=(
                lambda: self.camera_service.is_hardware_roi_applied
            ),
            camera_roi_current_fn=(
                lambda width, height, decimation:
                self.camera_service.is_hardware_roi_current(
                    width,
                    height,
                    decimation,
                )
            ),
            confirm_fn=self._confirm_motion,
            status_fn=self.status_message.emit,
        )
        self.shutdown_service = ApplicationShutdownService(
            config_service=self.config_service,
            live_view_service=self.live_view_service,
            focus_task_service=self.focus_task_service,
            motion_service=self.motion_service,
            camera_service=self.camera_service,
            controller=self.controller,
            message_fn=self._log,
            status_fn=self.status_message.emit,
        )
        self._connect_signals()  # ★ 新增：连接所有信号

    def _log(self, text: str):
        self.log_panel.append(text)


    # ===================================================
    # 信号连接
    # ===================================================
    def _connect_signals(self):
        self.status_message.connect(self._show_status) #更新状态栏
        self.inspection_panel.model_load_requested.connect(
            self._on_inspection_model_load
        )
        self.inspection_panel.focus_start_requested.connect(
            self._on_start_focus_from_inspection
        )
        self.inspection_panel.offline_image_test_requested.connect(
            self._on_offline_inspection_image
        )
        self.inspection_panel.inspection_config_save_requested.connect(
            self._on_inspection_config_save
        )
        self.inspection_panel.inspection_config_invalid.connect(
            lambda message: self._log(f"[检测] 配置输入错误: {message}")
        )
        self.inspection_panel.original_image_saved.connect(
            lambda path: self._log(f"[检测] 原始最终图已保存: {path}")
        )
        self.inspection_panel.original_image_save_failed.connect(
            lambda message: self._log(f"[检测] 原始最终图保存失败: {message}")
        )
        self.inspection_panel.inspection_recalculate_requested.connect(
            self._on_inspection_recalculate
        )
        self.inspection_panel.circle_redetection_requested.connect(
            self._on_circle_redetection_request
        )
        self.inspection_panel.circle_confirmation_requested.connect(
            self._on_circle_confirmation_request
        )
        self.inspection_service.model_loading.connect(
            self.inspection_panel.set_model_loading
        )
        self.inspection_service.model_loaded.connect(
            self._on_inspection_model_loaded
        )
        self.inspection_service.model_load_failed.connect(
            self._on_inspection_model_load_failed
        )
        self.inspection_service.image_pending.connect(
            lambda task_id: self._log(f"[检测] 图像已排队，等待模型: {task_id}")
        )
        self.inspection_service.inspection_started.connect(
            lambda task_id: self.status_message.emit(
                f"正在后台检测最终图像 ({task_id})"
            )
        )
        self.inspection_service.inspection_finished.connect(
            self._on_inspection_finished
        )
        self.inspection_service.inspection_image_saved.connect(
            lambda task_id, path: self._log(f"[检测] {task_id} 结果图已保存: {path}")
        )
        self.inspection_service.inspection_image_save_failed.connect(
            lambda task_id, message: self._log(f"[检测] {task_id} 结果图保存失败: {message}")
        )
        self.inspection_service.image_inspection_visual_ready.connect(
            self._on_image_inspection_visual_ready
        )
        self.inspection_service.inspection_visual_ready.connect(
            self._on_inspection_visual_ready
        )
        self.inspection_service.image_circle_redetection_finished.connect(
            self._on_image_circle_redetection_finished
        )
        self.inspection_service.circle_redetection_finished.connect(
            self._on_circle_redetection_finished
        )
        self.param_panel.motion_connect_btn.clicked.connect(
            self._on_motion_connect
        )
        self.param_panel.camera_connect_btn.clicked.connect(
            self._on_camera_connect
        )
        self.param_panel.camera_roi_apply_btn.clicked.connect(
            self._on_apply_camera_roi
        )
        self.param_panel.motion_reset_btn.clicked.connect(
            lambda _checked=False: self.motion_service.clear_alarm()
        )
        self.param_panel.motion_servo_btn.clicked.connect(
            self._on_motion_servo
        )
        self.param_panel.motion_home_btn.clicked.connect(
            self._on_motion_home
        )
        self.param_panel.motion_stop_btn.clicked.connect(
            lambda _checked=False: self.motion_service.stop_motion()
        )
        self.param_panel.template_load_btn.clicked.connect(
            lambda _checked=False: self.result_presenter.load_template()
        )
        self.param_panel.start_btn.clicked.connect(
            lambda _checked=False: self._start_focus_task()
        )
        self.image_widget.live_btn.clicked.connect(self._on_toggle_live_view)
        self.param_panel.stop_btn.clicked.connect(self.controller.request_cancel) #停止

        self.focus_task_service.finished.connect(
            self._on_focus_finished
        )
        self.focus_task_service.best_frame_ready.connect(
            self._on_best_frame_ready
        )
        self.focus_task_service.error.connect(
            self._on_focus_error
        )
        self.focus_task_service.preview.connect(
            self.result_presenter.present_preview
        )
        self.shutdown_service.retry_requested.connect(
            self.close,
            Qt.QueuedConnection,
        )


    # ---------- 槽函数（新增） ----------
    def _show_status(self, text: str):
        self.statusBar().showMessage(text)

    def _on_inspection_model_load(self, model_path: str):
        """响应检测页的手动加载请求，实际加载交给后台服务。"""

        self.inspection_config.model_path = model_path
        self.inspection_config.circle.model_path = (
            self.inspection_panel.selected_circle_model_path
        )

        try:
            accepted = self.inspection_service.load_model(
                model_path,
                self.inspection_config.circle.model_path,
                self.inspection_config,
            )
        except RuntimeError as error:
            QMessageBox.warning(self, "检测模型", str(error))
            self._log(f"[检测] {error}")
            return

        if not accepted:
            self._log("[检测] 当前无法提交模型加载请求")

    def _on_start_focus_from_inspection(self):
        """检测页的快捷按钮：复用对焦过程页既有的启动与校验逻辑。"""

        self._start_focus_task()

    def _start_focus_task(self):
        """从任一页面启动对焦，并同步管理检测页快捷按钮状态。"""

        if self.focus_task_service.is_running:
            self.status_message.emit("对焦任务正在运行，请勿重复启动")
            return

        if self.focus_run_service.start():
            self.inspection_panel.start_focus_btn.setEnabled(False)

    def _on_inspection_config_save(self, config: InspectionConfig):
        """校验并原子保存检测配置；失败时保留上一份有效配置。"""

        errors = config.validate()
        if errors:
            self.status_message.emit("检测配置有误，未保存")
            self._log("[检测] 配置校验未通过，未覆盖原配置:")
            for message in errors:
                self._log(f"[检测] - {message}")
            return

        try:
            self.inspection_config_store.save(config)
        except (OSError, TypeError, ValueError) as error:
            self.status_message.emit("检测配置保存失败")
            self._log(f"[检测] 配置保存失败: {error}")
            return

        self.inspection_config = config
        self.inspection_panel.accept_inspection_config(config)
        self.status_message.emit("检测配置已保存")
        self._log(
            f"[检测] 检测配置已保存: {self.inspection_config_store.path}"
        )

    def _on_inspection_model_loaded(self, model_path: str, metadata):
        """把后台加载成功信息显示到检测页并写入日志。"""

        self.inspection_panel.set_model_loaded(model_path, metadata)
        self._log(f"[检测] 语义分割模型已加载: {model_path}")
        if isinstance(metadata, dict):
            segmentation_device = metadata.get("inference_device") or "自动选择"
            circle_device = metadata.get("circle_inference_device") or "自动选择"
            self._log(
                f"[检测] 推理设备: 分割={segmentation_device}，"
                f"找圆={circle_device}"
            )
            self._log(
                "[检测] 找圆模型已加载并预热（最长边 1024）: "
                f"{metadata.get('circle_model_path', '')}"
            )

    def _on_inspection_model_load_failed(self, message: str):
        """把后台加载失败信息显示到检测页和底部日志。"""

        self.inspection_panel.set_model_load_failed(message)
        self._log(f"[检测] 模型加载失败: {message}")

    def _on_offline_inspection_image(
        self,
        image_path: str,
        config: InspectionConfig,
    ):
        """读取一张本地图并复用现有异步检测服务。"""

        if not isinstance(config, InspectionConfig):
            self._log("[检测] 本地图片测试配置类型无效")
            return
        errors = config.validate()
        if errors:
            self.status_message.emit("本地图片检测参数有误")
            self._log(f"[检测] 本地图片未提交: {errors[0]}")
            return

        try:
            image = load_inspection_image(image_path)
        except ValueError as error:
            self.status_message.emit("本地检测图片读取失败")
            self._log(f"[检测] 本地图片读取失败: {error}")
            QMessageBox.warning(
                self,
                "本地图片读取失败",
                str(error),
            )
            return

        try:
            task_id = self.inspection_service.submit_image(
                image, config, original_image_path=image_path,
            )
        except (TypeError, ValueError) as error:
            self.status_message.emit("本地检测图片提交失败")
            self._log(f"[检测] 本地图片提交失败: {error}")
            return

        if not task_id:
            self._log("[检测] 本地图片未提交，可能与当前任务重复")
            return

        absolute_path = os.path.abspath(image_path)
        self.status_message.emit(
            f"本地图片已提交检测：{os.path.basename(absolute_path)}"
        )
        self._log(
            f"[检测] 已提交本地图片，任务号: {task_id}，"
            f"路径: {absolute_path}"
        )

    def _on_focus_finished(self, result):
        """展示结果，并刷新任务结束后的轴位置和伺服状态。"""

        is_early_continuous_result = (
            self._continuous_best_frame_presented
            and getattr(result, "quality", "") == "continuous_best_frame"
        )
        if is_early_continuous_result:
            self.result_presenter.complete_continuous_return(result)
        else:
            self.result_presenter.handle_finished(result)
            self._submit_final_image_for_inspection(result)
        self._continuous_best_frame_presented = False
        self.inspection_panel.start_focus_btn.setEnabled(True)
        if self.motion_service.backend is not None:
            self.motion_service.refresh_state()

    def _on_best_frame_ready(self, event):
        """最佳帧确定后立即显示并提交检测；轴仍由任务线程回起点。"""

        self._continuous_best_frame_presented = True
        self.result_presenter.present_best_frame_ready(event)
        self._submit_image_for_inspection(
            getattr(event, "image", None),
            original_image_path=getattr(event, "final_image_path", None),
        )

    def _submit_final_image_for_inspection(self, result):
        """将成功搜索得到的最终图旁路提交给独立检测服务。"""

        if getattr(result, "action", "") != "search":
            return
        if getattr(result, "rc", 1) != 0:
            return

        final_image = getattr(result, "final_image", None)
        self._submit_image_for_inspection(
            final_image,
            original_image_path=getattr(result, "final_image_path", None),
        )

    def _submit_image_for_inspection(self, image, *, original_image_path=None):
        """将已确定的原始最佳图提交给独立检测服务。"""

        if image is None:
            return

        try:
            task_id = self.inspection_service.submit_image(
                image,
                self.inspection_config,
                original_image_path=original_image_path,
            )
        except (TypeError, ValueError) as error:
            self._log(f"[检测] 最终图提交失败: {error}")
            return

        if task_id:
            self._log(f"[检测] 已提交最终图，任务号: {task_id}")

    def _on_inspection_finished(self, task_id: str, result):
        """接收后台检测结果，先更新判定摘要并写入日志。"""

        status = getattr(getattr(result, "status", None), "value", "PENDING")
        verdict = {"PASS": "合格", "FAIL": "不合格", "ERROR": "检测错误"}.get(status, "待确认")
        has_image_result = (
            getattr(self._inspection_image_result, "image_id", "")
            == task_id
        )
        if not has_image_result:
            self.inspection_panel.verdict_label.setText(verdict)
            self.inspection_panel.verdict_detail_label.setText(
                f"任务：{task_id}"
            )
        self._log(f"[检测] 任务 {task_id} 完成，结果: {verdict}")

        error = str(getattr(result, "error", "") or "").strip()
        if error:
            self._log(f"[检测] 错误: {error}")
        for message in list(getattr(result, "warnings", []) or []):
            self._log(f"[检测] 警告: {message}")
        for message in list(getattr(result, "failure_reasons", []) or []):
            self._log(f"[检测] 失败原因: {message}")

    def _on_inspection_visual_ready(
        self,
        task_id: str,
        original_image,
        result,
        config,
    ):
        """按任务号对应的原图绘制检测轮廓并刷新结果页。"""

        try:
            self._inspection_current_task_id = task_id
            self._inspection_current_image = original_image
            # 保留首次推理和找圆的原始输出。后续参数复判始终复用它，
            # 不把某次复判结果当作下一次的模型输入。
            self._inspection_source_result = result
            if (
                getattr(self._inspection_image_result, "image_id", "")
                != task_id
            ):
                self.inspection_panel.present_inspection_result(
                    task_id,
                    original_image,
                    result,
                    config,
                )
        except (TypeError, ValueError) as error:
            self._log(f"[检测] 结果绘制失败: {error}")

    def _on_image_inspection_visual_ready(
        self,
        task_id: str,
        original_image,
        result,
        _config,
    ):
        """保留完整多圆结果，当前单圆界面仍使用兼容结果绘制。"""

        self._inspection_current_task_id = task_id
        self._inspection_current_image = original_image
        self._inspection_image_result = result
        try:
            self.inspection_panel.present_image_inspection_result(
                task_id,
                original_image,
                result,
                _config,
            )
        except (TypeError, ValueError) as error:
            self._log(f"[检测] 多端面结果绘制失败: {error}")

    def _on_circle_redetection_request(self, config: InspectionConfig):
        """将当前图提交给检测线程，重新找圆并按新 ROI 重跑分割。"""

        if (
            self._inspection_current_image is None
            or self._inspection_source_result is None
            or not self._inspection_current_task_id
        ):
            self._log("[检测] 当前没有可重新找圆的检测图")
            return
        try:
            accepted = self.inspection_service.redetect_circle(
                self._inspection_current_task_id,
                self._inspection_current_image,
                self._inspection_source_result,
                config,
            )
        except (TypeError, ValueError) as error:
            self._log(f"[检测] 重新找圆提交失败: {error}")
            return
        if not accepted:
            self._log("[检测] 检测服务正忙，无法重复提交重新找圆")
            return

        self._inspection_circle_request_config = config
        self.inspection_panel.set_circle_redetecting()
        self.status_message.emit("正在后台重新找圆并检测新 ROI")

    def _on_circle_redetection_finished(self, task_id: str, result):
        """接收重新找圆和 ROI 重推理结果并刷新当前图。"""

        if task_id != self._inspection_current_task_id:
            return
        config = self._inspection_circle_request_config
        self._inspection_circle_request_config = None
        if config is None:
            return

        has_image_result = (
            getattr(self._inspection_image_result, "image_id", "")
            == task_id
        )
        self._inspection_source_result = result
        if getattr(getattr(result, "status", None), "value", "") == "ERROR":
            message = str(getattr(result, "error", "") or "重新找圆失败")
            if not has_image_result:
                self.inspection_panel.set_circle_redetection_failed(message)
            self._log(f"[检测] 重新找圆失败: {message}")
            self.status_message.emit("重新找圆失败")
            return

        if not has_image_result:
            try:
                self.inspection_panel.present_recalculated_result(
                    task_id,
                    result,
                    config,
                )
            except (TypeError, ValueError) as error:
                self.inspection_panel.set_circle_redetection_failed(str(error))
                self._log(f"[检测] 重新找圆结果绘制失败: {error}")
                return
        for message in list(getattr(result, "warnings", []) or []):
            self._log(f"[检测] 找圆警告: {message}")
        for message in list(getattr(result, "failure_reasons", []) or []):
            self._log(f"[检测] 重新找圆判定原因: {message}")
        circle_count = len(getattr(result, "circle_candidates", []) or [])
        self._log(f"[检测] 当前图重新找圆完成，候选数: {circle_count}")
        self.status_message.emit("当前图重新找圆完成")

    def _on_image_circle_redetection_finished(self, task_id: str, result):
        """保留重新找圆和 ROI 重推理后的完整多圆结果。"""

        if task_id == self._inspection_current_task_id:
            self._inspection_image_result = result
            config = (
                self._inspection_circle_request_config
                or self.inspection_config
            )
            try:
                self.inspection_panel.present_image_recalculated_result(
                    task_id,
                    result,
                    config,
                )
            except (TypeError, ValueError) as error:
                self._log(f"[检测] 多端面重新找圆结果绘制失败: {error}")

    def _on_circle_confirmation_request(self, config: InspectionConfig):
        """确认最高评分候选圆并轻量复判，不重新运行 Hough。"""

        detected_task_count = int(getattr(
            self._inspection_image_result,
            "expected_circle_count",
            config.circle.expected_circle_count,
        ))
        if (
            detected_task_count != 1
            or config.circle.expected_circle_count != 1
        ):
            self._log(
                "[检测] 当前多圆结果暂不支持在单圆界面人工确认；"
                "请调整找圆参数后重新检测"
            )
            self.status_message.emit("多圆结果需在后续多圆界面逐个确认")
            return

        source = self._inspection_source_result
        if source is None or not source.circle_candidates:
            self._log("[检测] 当前没有可确认的候选圆")
            return
        selected_index = source.selected_circle_index
        if not isinstance(selected_index, int) or not (
            0 <= selected_index < len(source.circle_candidates)
        ):
            self._log("[检测] 当前候选圆序号无效")
            return

        started = time.perf_counter()
        confirmed_source = copy.copy(source)
        confirmed_source.circle_confirmed = True
        try:
            result = self.inspection_recheck_engine.reevaluate(
                confirmed_source,
                mm_per_pixel=config.mm_per_pixel,
                region_rules=config.region_rules,
            )
        except (TypeError, ValueError) as error:
            self._log(f"[检测] 圆心确认复判失败: {error}")
            return
        result.timings_ms = copy.deepcopy(source.timings_ms)
        result.timings_ms["circle_confirmation_reevaluation"] = (
            time.perf_counter() - started
        ) * 1000.0
        self._inspection_source_result = result
        try:
            self.inspection_panel.present_recalculated_result(
                self._inspection_current_task_id,
                result,
                config,
            )
        except (TypeError, ValueError) as error:
            self._log(f"[检测] 圆心确认结果绘制失败: {error}")
            return
        for message in list(getattr(result, "failure_reasons", []) or []):
            self._log(f"[检测] 圆心确认判定原因: {message}")
        self._log("[检测] 已人工确认当前最高评分候选圆")
        self.status_message.emit("当前圆心已确认并完成复判")

    def _on_inspection_recalculate(self, config: InspectionConfig):
        """复用已有实例和圆候选，只重新执行规则统计与叠加绘制。"""

        detected_task_count = int(getattr(
            self._inspection_image_result,
            "expected_circle_count",
            config.circle.expected_circle_count,
        ))
        if (
            detected_task_count != 1
            or config.circle.expected_circle_count != 1
        ):
            self._log(
                "[检测] 当前单圆兼容界面不执行多圆参数复判；"
                "完整多圆复判将在多圆结果界面接入"
            )
            return

        source = self._inspection_source_result
        if source is None or not self._inspection_current_task_id:
            return

        started = time.perf_counter()
        try:
            result = self.inspection_recheck_engine.reevaluate(
                source,
                mm_per_pixel=config.mm_per_pixel,
                region_rules=config.region_rules,
            )
            result.timings_ms = copy.deepcopy(source.timings_ms)
            result.timings_ms["reevaluation"] = (
                time.perf_counter() - started
            ) * 1000.0
            self.inspection_panel.present_recalculated_result(
                self._inspection_current_task_id,
                result,
                config,
            )
            verdict = {
                "PASS": "合格",
                "FAIL": "不合格",
                "PENDING": "待确认",
                "ERROR": "检测错误",
            }.get(result.status.value, "待确认")
            self.status_message.emit(f"当前图参数复判完成：{verdict}")
        except (TypeError, ValueError) as error:
            self._log(f"[检测] 当前图参数复判失败: {error}")

    def _on_focus_error(self, error_text: str):
        """展示后台异常，并刷新运动控制器安全状态。"""

        self.result_presenter.handle_error(error_text)
        self._continuous_best_frame_presented = False
        self.inspection_panel.start_focus_btn.setEnabled(True)
        if self.motion_service.backend is not None:
            self.motion_service.refresh_state()

    def _confirm_motion(self, message: str) -> bool:
        """显示真实运动确认框，返回用户是否同意继续。"""

        answer = QMessageBox.question(
            self,
            "真实运动确认",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        return answer == QMessageBox.Yes

    def _on_motion_connect(self):
        """把用户的连接或断开请求交给MotionService。"""

        try:
            config = self.config_service.build_motion_config()
        except Exception as error:
            logger.exception("构造运动控制器配置失败")
            self.status_message.emit("运动控制器配置错误")
            QMessageBox.critical(
                self,
                "运动控制器配置错误",
                str(error),
            )
            return

        self.motion_service.toggle(config)

    def _on_camera_connect(self):
        """把用户的相机连接或断开请求交给CameraService。"""

        # 实时预览正在占用相机时，连接会因独占访问失败、
        # 断开会破坏预览线程正在使用的句柄，两种都先拒绝。
        if (
            self.live_view_service.is_active
            or self.live_view_service.is_running
        ):
            self.status_message.emit("请先停止实时预览，再连接或断开相机")
            QMessageBox.warning(
                self,
                "实时预览正在运行",
                "实时预览正在占用相机，请先停止实时预览。",
            )
            return

        # GUI当前没有相机索引设置项，与FocusConfig默认值保持一致。
        self.camera_service.toggle(0)

    def _on_apply_camera_roi(self):
        """把界面输入的居中宽高和降采样应用到当前相机。"""

        if (
            self.live_view_service.is_active
            or self.live_view_service.is_running
        ):
            self.status_message.emit(
                "请先停止实时预览，再应用相机 ROI"
            )
            QMessageBox.warning(
                self,
                "实时预览正在运行",
                "实时预览正在占用相机，请先停止实时预览。",
            )
            return

        if not self.camera_service.is_connected:
            message = "请先连接相机，再应用相机 ROI"
            self.status_message.emit(message)
            QMessageBox.warning(self, "相机未连接", message)
            return

        decimation_map = {
            "1x1": 1,
            "2x2": 2,
            "4x4": 4,
        }
        width = self.param_panel.work_roi_width_spin.value()
        height = self.param_panel.work_roi_height_spin.value()
        decimation = decimation_map.get(
            self.param_panel.decimation_combo.currentText(),
            1,
        )

        try:
            actual = self.camera_service.apply_hardware_roi(
                width,
                height,
                decimation,
            )
        except Exception as error:
            logger.exception("应用相机 ROI 失败")
            message = f"应用相机 ROI 失败: {error}"
            self.status_message.emit(message)
            QMessageBox.critical(self, "应用相机 ROI 失败", str(error))
            return

        x, y, actual_width, actual_height = actual
        self._log(
            f"[相机] 已应用居中 ROI: ({x},{y}) "
            f"{actual_width}x{actual_height}, decimation={decimation}"
        )
        self.status_message.emit(
            f"相机 ROI 已应用: {actual_width}x{actual_height}"
        )

    def _on_motion_servo(self):
        """手动使能前二次确认；去使能直接执行。"""

        state = self.motion_service.state
        if state.servo_enabled:
            self.motion_service.toggle_servo()
            return
        position = (
            "--"
            if state.position_um is None
            else f"{state.position_um:.2f} µm"
        )
        if self._confirm_motion(
            "即将手动使能直线电机。\n\n"
            f"当前位置: {position}\n"
            f"轴状态: {state.message}\n\n"
            "请确认机械区域安全，是否继续？"
        ):
            self.motion_service.toggle_servo()

    def _on_motion_home(self):
        """展示已验证参数并二次确认真实回零。"""

        state = self.motion_service.state
        config = self.motion_service.config
        if config is None:
            return
        position = (
            "--"
            if state.position_um is None
            else f"{state.position_um:.2f} µm"
        )
        message = (
            "即将执行真实回原点，程序将自动使能。\n\n"
            f"当前位置: {position}\n"
            "回零参数: 使用驱动器当前保存值（开始回零时读取）\n"
            "程序不会自动修改驱动器回零参数\n"
            f"超时: {config.home_timeout_s:g} s\n\n"
            "请确认急停可用且机械区域无人，是否继续？"
        )
        if self._confirm_motion(message):
            self.motion_service.home()

    # ===================================================
    # 总体布局
    # ===================================================
    def _build_ui(self):
        central = QWidget()                       # 中央容器（一个普通控件）
        root = QVBoxLayout(central)               # 整体垂直排：上面主体 + 下面日志

        self.main_tabs = QTabWidget()

        focus_page = QWidget()
        top = QHBoxLayout(focus_page)             # 对焦页：左面板 + 图像区

        self.param_panel = ParamPanel()
        top.addWidget(self.param_panel)   # 左面板（自身固定宽度）

        self.image_widget = ImageWidget()
        top.addWidget(self.image_widget, 1) # 图像区，数字 1 = 占满剩余空间

        self.curve_panel = CurvePanel()
        # 新版连续精扫不再显示 NCC 清晰度曲线。
        # 保留对象供 ResultPresenter 和旧标定/NCC 兼容代码调用，
        # 但不把它加入主布局，避免占用图像视图空间。

        self.main_tabs.addTab(focus_page, "对焦过程")

        self.inspection_panel = InspectionPanel()
        self.main_tabs.addTab(self.inspection_panel, "检测结果")

        root.addWidget(self.main_tabs, 1)         # 主体占垂直方向剩余空间

        self.log_panel = LogPanel()   # 底部日志
        root.addWidget(self.log_panel)

        self.setCentralWidget(central)            # 把中央容器装进主窗口
    # ---------- 实时预览 ----------
    def _on_toggle_live_view(self):
        """收集预览参数，并把启停请求交给 LiveViewService。"""

        source = (
            "real"
            if self.param_panel.mode_combo.currentText() == "真实"
            else "sim"
        )

        dec_map = {"1x1": 1, "2x2": 2, "4x4": 4}

        camera_params = {
            "exposure_us": self.param_panel.exposure_spin.value(),
            "gain_db": self.param_panel.gain_spin.value(),
            "dec": dec_map[self.param_panel.decimation_combo.currentText()],
            "work_roi_width_px": (
                self.param_panel.work_roi_width_spin.value()
            ),
            "work_roi_height_px": (
                self.param_panel.work_roi_height_spin.value()
            ),
        }

        self.live_view_service.toggle(source, camera_params)

    def closeEvent(self, event):
        """安全关闭主窗口。"""

        # 检测模型可能正在独立线程中加载或推理，先等待其自然退出。
        if not self.inspection_service.is_shutdown_complete:
            self._inspection_close_requested = True
            self.inspection_service.begin_shutdown()
            self.status_message.emit("正在停止检测后台线程，完成后将自动关闭")
            if not self._inspection_shutdown_connected:
                self.inspection_service.shutdown_ready.connect(
                    self.close,
                    Qt.QueuedConnection,
                )
                self._inspection_shutdown_connected = True
            event.ignore()
            return

        if not self.shutdown_service.try_shutdown():
            event.ignore()
            return

        if self._qt_log_handler is not None:
            remove_qt_log_handler(self._qt_log_handler)
            self._qt_log_handler = None

        event.accept()


    def _config_path(self) -> str:
        return os.path.join(PROJECT_ROOT, "gui", "config.json")
