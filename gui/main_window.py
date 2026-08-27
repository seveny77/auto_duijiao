# -*- coding: utf-8 -*-
"""主窗口模块（课程 A：代码修缮）"""
import logging
from gui.app.services.qt_log_handler import (
    install_qt_log_handler,
    remove_qt_log_handler,
)
import os
from gui.app.widgets.image_view import ImageWidget
from gui.app.widgets.curve_panel import CurvePanel
from gui.app.widgets.log_panel import LogPanel
from gui.app.services.config_service import ConfigService
from gui.app.services.controller import AppController
from gui.app.services.ct_logger import CtLogger
from gui.app.services.result_presenter import ResultPresenter
from gui.app.services.motion_service import MotionService
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
        self.setWindowTitle("自动对焦系统")
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
        )

        self.config_service = ConfigService(
            path=self._config_path(),
            panel=self.param_panel,
            project_root=PROJECT_ROOT,
        )
        self.config_service.load()
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
            confirm_fn=self._confirm_motion,
            status_fn=self.status_message.emit,
        )
        self.shutdown_service = ApplicationShutdownService(
            config_service=self.config_service,
            live_view_service=self.live_view_service,
            focus_task_service=self.focus_task_service,
            motion_service=self.motion_service,
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
        self.param_panel.motion_connect_btn.clicked.connect(
            self._on_motion_connect
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
            lambda _checked=False: self.focus_run_service.start()
        )
        self.image_widget.live_btn.clicked.connect(self._on_toggle_live_view)
        self.param_panel.stop_btn.clicked.connect(self.controller.request_cancel) #停止

        self.focus_task_service.finished.connect(
            self._on_focus_finished
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

    def _on_focus_finished(self, result):
        """展示结果，并刷新任务结束后的轴位置和伺服状态。"""

        self.result_presenter.handle_finished(result)
        if self.motion_service.backend is not None:
            self.motion_service.refresh_state()

    def _on_focus_error(self, error_text: str):
        """展示后台异常，并刷新运动控制器安全状态。"""

        self.result_presenter.handle_error(error_text)
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
        top = QHBoxLayout()                       # 主体水平排：左面板 + 图像区

        self.param_panel = ParamPanel()
        top.addWidget(self.param_panel)   # 左面板（自身固定宽度）

        self.image_widget = ImageWidget()
        top.addWidget(self.image_widget, 1) # 图像区，数字 1 = 占满剩余空间

        self.curve_panel = CurvePanel()
        top.addWidget(self.curve_panel)

        root.addLayout(top, 1)                    # 主体占垂直方向剩余空间

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
        }

        self.live_view_service.toggle(source, camera_params)

    def closeEvent(self, event):
        """安全关闭主窗口。"""

        if not self.shutdown_service.try_shutdown():
            event.ignore()
            return

        if self._qt_log_handler is not None:
            remove_qt_log_handler(self._qt_log_handler)
            self._qt_log_handler = None

        event.accept()


    def _config_path(self) -> str:
        return os.path.join(PROJECT_ROOT, "gui", "config.json")
