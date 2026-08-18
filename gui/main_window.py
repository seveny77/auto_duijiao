# -*- coding: utf-8 -*-
"""主窗口模块（课程 A：代码修缮）"""
import time
import os
import cv2
from gui.app.widgets.image_view import ImageWidget
from gui.app.widgets.curve_panel import CurvePanel
from gui.app.widgets.log_panel import LogPanel
from gui.app.services.config_service import ConfigService
from gui.app.services.controller import AppController
from gui.app.services.ct_logger import CtLogger
from backend.config import FocusConfig
from PyQt5.QtCore import QThread, pyqtSignal,Qt
from gui.app.workers.verify_worker import VerifyWorker
import threading
from gui.app.workers.live_view_worker import LiveViewWorker
from gui.app.workers.plc_connect_worker import PlcConnectWorker
from gui.app.widgets.param_panels import ParamPanel
from PyQt5.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,

)

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
        self.plc = None
        self.setWindowTitle("自动对焦系统")
        self.resize(1280, 800)
        self.statusBar().showMessage("就绪")
        self._build_ui()          # 把界面搭建交给单独的方法
        self.controller = AppController(
            widgets_to_lock=self.param_panel.lock_widgets(),
            start_btn=self.param_panel.start_btn,
            stop_btn=self.param_panel.stop_btn,
            live_btn=self.image_widget.live_btn,
            log_fn=self._log,
            status_fn=self.status_message.emit,
        )
        self.ct_logger = CtLogger(self._log)
        self.config_service = ConfigService(self._config_path(), self.param_panel, self._log)
        self.config_service.load()
        self._connect_signals()  # ★ 新增：连接所有信号
        self._init_worker()
        self._live_active = False  # 预览是否在运行
        self._last_live_ts = 0.0  # 上次刷帧的时间戳
        # 标定、粗扫、精扫过程预览的上次刷新时间。
        self._last_process_preview_ts = 0.0
        self.live_thread = None  # 取流线程
        self._init_detect_model()

    def _log(self, text: str):
        self.log_panel.append(text)

    def _init_detect_model(self):
        """启动时在主线程加载 YOLO（CUDA），之后所有搜索共用这一个实例。"""
        try:
            from verify_ncc_full import build_parser
            from ultralytics import YOLO
            path = vars(build_parser().parse_args([]))["detect_model"]
            if not os.path.exists(path):
                self._log(f"[警告] YOLO 模型不存在: {path}")
                self._detect_model = None
                return
            self._detect_model = YOLO(path)  # ★ 主线程加载
            self._log(f"YOLO 模型已加载（主线程）: {path}")
        except Exception as e:
            self._log(f"[错误] YOLO 模型加载失败: {e}")
            self._detect_model = None

    def _init_worker(self):
        self.thread = QThread()  # 线程容器
        self.worker = VerifyWorker()  # 工作对象
        self.worker.moveToThread(self.thread)  # 把工作对象搬进线程
        self.worker.log.connect(self._log)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.thread.start()  # 启动线程
        self.worker.preview.connect(self._on_process_preview,Qt.QueuedConnection,)
    # ===================================================
    # 信号连接
    # ===================================================
    def _connect_signals(self):
        self.status_message.connect(self._show_status) #更新状态栏
        self.param_panel.plc_connect_btn.clicked.connect(self._on_plc_connect) #plc连接
        self.param_panel.template_load_btn.clicked.connect(self._on_template_load) #模板加载
        self.param_panel.start_btn.clicked.connect(self._on_start_clicked)
        self.image_widget.live_btn.clicked.connect(self._on_toggle_live_view)
        self.param_panel.stop_btn.clicked.connect(self.controller.request_cancel) #停止

    # ---------- 槽函数（新增） ----------
    def _show_status(self, text: str):
        self.statusBar().showMessage(text)


    def _on_plc_connect(self):
        if self.plc is not None:  # 已连接 → 切换为断开
            self._disconnect_plc()
            return

        host = self.param_panel.plc_ip_edit.text().strip()
        port = self.param_panel.plc_port_spin.value()
        self._log(f"正在连接 PLC {host}:{port} ...")
        self.param_panel.plc_connect_btn.setEnabled(False)  # 防重复点击
        self.param_panel.plc_connect_btn.setText("连接中...")

        # 后台线程连接，避免界面卡死
        self.plc_worker = PlcConnectWorker(host, port)
        self.plc_worker.connected.connect(self._on_plc_connected)
        self.plc_worker.failed.connect(self._on_plc_failed)
        self.plc_thread = threading.Thread(
            target=self.plc_worker.run, daemon=True)
        self.plc_thread.start()

    def _on_plc_connected(self, payload):
        plc, stroke = payload  # 解包信号带回来的对象
        self.plc = plc
        self.param_panel.plc_connect_btn.setEnabled(True)
        self.param_panel.plc_connect_btn.setText("断开 PLC")
        self.param_panel.plc_stroke_label.setText(f"行程: {stroke[0]} ~ {stroke[1]} µm")
        self._log(f"PLC 已连接: {self.param_panel.plc_ip_edit.text().strip()}:{self.param_panel.plc_port_spin.value()}")
        self._log(f"行程范围: {stroke[0]} ~ {stroke[1]} µm")
        self.status_message.emit("PLC 已连接")

    def _on_plc_failed(self, msg):
        self.param_panel.plc_connect_btn.setEnabled(True)  # 恢复现场
        self.param_panel.plc_connect_btn.setText("连接 PLC")
        self._log(f"[错误] PLC 连接失败: {msg}")
        self.status_message.emit("PLC 连接失败")

    def _disconnect_plc(self):
        try:
            self.plc.disconnect()
        except Exception as e:
            self._log(f"[警告] 断开 PLC 异常: {e}")
        self.plc = None
        self.param_panel.plc_connect_btn.setText("连接 PLC")
        self.param_panel.plc_stroke_label.setText("行程: 未连接")
        self._log("PLC 已断开")




    def _on_template_load(self):
        path = _resolve_path(self.param_panel.template_edit.text().strip())   # 先解析
        if not path:
            self._log("[错误] 模板路径为空")
            return
        # try/except：程序出错不崩溃，而是捕获并记录
        try:
            from focus_template import FocusTemplate   # 用到时才导入
            template = FocusTemplate.load(path)
        except FileNotFoundError:
            self._log(f"[错误] 文件不存在: {path}")
            return
        except Exception as e:
            self._log(f"[错误] 模板加载失败: {e}")
            return

        self._log(f"模板加载成功: 峰位置={template.peak_position}, "
                  f"FWHM={template.peak_width:.2f}")
        self.status_message.emit("模板已加载")

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

    def _collect_params(self) -> dict:
        from verify_ncc_full import build_parser
        cfg = FocusConfig(**vars(build_parser().parse_args([])))   # parser 默认值做基底
        action_map = {"搜索对焦": "search", "图像标定": "calibrate"}  # ★ 离线标定→图像标定
        mode_map = {"真实": "real", "仿真": "sim"}  # ★ 新增：中文→英文
        dec_map = {"1x1": 1, "2x2": 2, "4x4": 4}  # ★ binning_map → dec_map
        cfg.action = action_map[self.param_panel.action_combo.currentText()]
        cfg.mode = mode_map[self.param_panel.mode_combo.currentText()]
        cfg.yes = self.param_panel.skip_confirm_check.isChecked()
        cfg.template = _resolve_path(self.param_panel.template_edit.text().strip())
        cfg.plc_host = self.param_panel.plc_ip_edit.text().strip()
        cfg.plc_port = self.param_panel.plc_port_spin.value()
        cfg.exposure_us = self.param_panel.exposure_spin.value()
        cfg.gain_db = self.param_panel.gain_spin.value()
        cfg.coarse_binning = dec_map[self.param_panel.decimation_combo.currentText()]
        cfg.coarse_downsample = "decimation"
        cfg.coarse_step_um = self.param_panel.coarse_step_spin.value()
        cfg.fine_step_um = self.param_panel.fine_step_spin.value()
        cfg.fine_half_steps = self.param_panel.fine_half_spin.value()
        cfg.search_start_um = self.param_panel.search_start_spin.value()
        cfg.search_span_um = self.param_panel.search_span_spin.value()
        cfg.save_images = self.param_panel.save_edit.text().strip() or None
        cfg.calibrate_step_um = self.param_panel.calibrate_step_spin.value()
        cfg.calibrate_downsample = self.param_panel.calibrate_ds_combo.currentText().split()[0]
        cfg.calibrate_factor = int(self.param_panel.calibrate_ds_combo.currentText().split()[1])
        return cfg

    def _on_start_clicked(self):
        if self._live_active:  # 预览占用相机，先停掉
            self._stop_live_view()
        self.curve_panel.clear_curve() # 清空曲线
        self._last_process_preview_ts = 0.0 # # 新任务重新开始计算过程预览刷新时间。
        action = self.param_panel.action_combo.currentText()
        mode = self.param_panel.mode_combo.currentText()
        self._log(f"开始执行: 动作={action}, 模式={mode}")
        self.controller.new_cancel_event()  # ★ 每次运行新建停止标记
        cfg = self._collect_params()
        cfg.cancel_event = self.controller.cancel_event  # ★ 传给后台
        cfg.detect_model_obj = getattr(self, "_detect_model", None)  # ★ 新增
        self.controller.set_state(self.controller.STATE_RUNNING)
        self.worker.start.emit(cfg)

    # ---------- 实时预览 ----------
    def _on_toggle_live_view(self):
        if self._live_active:
            self._stop_live_view()
        else:
            self._start_live_view()

    def _start_live_view(self):

        # 复用流程控制里的模式：sim 用磁盘图片，real 用相机
        source = (
            "real"
            if self.param_panel.mode_combo.currentText() == "真实"
            else "sim"
        )
        self._log(f"开始实时预览（{source} 源）")

        self._live_active = True
        self._live_stop = threading.Event()  # 共享停止标记
        dec_map = {"1x1": 1, "2x2": 2, "4x4": 4}
        camera_params = {
            "exposure_us": self.param_panel.exposure_spin.value(),
            "gain_db": self.param_panel.gain_spin.value(),
            "dec": dec_map[self.param_panel.decimation_combo.currentText()],  # ★ binning → dec
        }

        self.live_worker = LiveViewWorker(source, PROJECT_ROOT, self._live_stop,camera_params)
        # 跨线程信号：取流线程发 frame，Qt 自动排队送到 GUI 线程
        self.live_worker.frame.connect(self._on_live_frame)
        self.live_worker.state.connect(self._on_live_state)
        self.live_worker.error.connect(self._on_live_error)

        self.live_thread = threading.Thread(
            target=self.live_worker.start, daemon=True)
        self.live_thread.start()

    def _stop_live_view(self):
        if self.live_thread is None:
            return
        self._log("正在停止实时预览...")
        self._live_stop.set()  # ① 打开停止开关
        self.live_thread.join(timeout=2)  # ② 等线程退出（最多 2 秒）
        self.live_thread = None
        self.live_worker = None
        self._live_active = False
        self.image_widget.live_btn.setText("开始实时预览")

    def _on_live_frame(self, img):
        # 帧率限制：50ms 内只刷一次，防止界面卡顿
        now = time.monotonic()
        if now - self._last_live_ts < 0.05:
            return
        self._last_live_ts = now
        self.image_widget.show_frame(img)

    def _on_process_preview(
            self,
            img,
            phase: str,
            sequence: int,
            score: float,
    ):
        """显示标定、粗扫、精扫过程中的抽样帧。"""

        if img is None:
            return

        # GUI 侧再做一层保护性限频。
        #
        # 主要限频仍将在 PhaseCollector 中完成；
        # 这里防止异常情况下信号过多导致 GUI 卡顿。
        now = time.monotonic()

        if now - self._last_process_preview_ts < 0.05:
            return

        self._last_process_preview_ts = now

        # 后端使用稳定的英文阶段名，
        # GUI 负责把它翻译成人能看懂的中文。
        phase_labels = {
            "calibrate": "标定",
            "coarse": "粗扫",
            "fine": "精扫",
        }

        phase_text = phase_labels.get(
            phase,
            phase,
        )

        # 显示图像。
        #
        # 不传 reset_view=True，因此用户在扫描过程中
        # 使用滚轮放大后，后续帧不会重置缩放。
        self.image_widget.show_frame(img)

        # sequence 在程序中从 0 开始，
        # 给用户显示时加 1，更符合日常习惯。
        self.status_message.emit(
            f"{phase_text}："
            f"第 {sequence + 1} 帧，"
            f"清晰度 {score:.1f}"
        )

    def _on_live_state(self, text):
        if text == "connecting":
            self._log("正在连接相机...")
        elif text == "started":
            self._log("实时预览已启动")
            self.image_widget.live_btn.setText("停止实时预览")
        elif text == "stopped":
            self._log("实时预览已停止")
            self.image_widget.live_btn.setText("开始实时预览")
            self._live_active = False

    def _on_live_error(self, text):
        self._log(f"[错误] 实时预览: {text}")
        self._stop_live_view()

    def _on_finished(self,result):
        self.controller.set_state(self.controller.STATE_DONE)
        if result.rc != 0:
            if "取消" in str(result.error or ""):
                self._log("[已取消] 流程被用户停止")
            else:
                self._log(f"[失败] {result.error or '未知错误'}")
            return
        if result.action == "search":
            self._log(f"预测峰={result.predicted_peak_um}µm  "
                      f"quality={result.quality}  定拍 index={result.move_index}")
            self.ct_logger.log(result.ct_ms)
            self.curve_panel.plot_points(result.coarse_points, "粗扫", "#1f77b4")
            self.curve_panel.plot_points(result.fine_points, "精扫", "#ff7f0e")
            self.curve_panel.plot_peak(result.predicted_peak_um)
            if result.final_image is not None and result.roi is not None:
                x, y, w, h = result.roi
                cv2.rectangle(result.final_image, (x, y), (x + w, y + h), (0, 255, 0), 3)
                cv2.putText(result.final_image, f"ROI {w}x{h} ({result.roi_src})",
                            (x, max(y - 12, 24)),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)
                self._show_image(result.final_image, "定拍全幅帧（ROI 已标注）")
            elif result.fine_best_image is not None:
                self._show_image(result.fine_best_image, f"精扫最佳帧 index={result.fine_best}")
            self.status_message.emit("搜索完成")
        elif result.action == "calibrate":
            path = _resolve_path(self.param_panel.template_edit.text().strip())
            self._log(f"标定完成: 模板已保存到 {path}")
            self._log(f"峰 index={result.peak_position}, "
                      f"FWHM={result.peak_width:.2f}, 峰位置={result.peak_um}µm")
            self.ct_logger.log(result.ct_ms)
            # ★ 把刚生成的模板曲线画出来
            try:
                from focus_template import FocusTemplate
                t = FocusTemplate.load(path)
                start = t.meta.get("start_um", 0)
                step = t.meta.get("step_um", 1)
                pts = [(start + (i + 1) * step, s) for i, s in enumerate(t.curve)]
                self.curve_panel.plot_points(pts, "标定曲线", "#2ca02c")
                self.curve_panel.plot_peak(start + (t.peak_position + 1) * step, "模板峰")
            except Exception as e:
                self._log(f"[警告] 标定曲线绘制失败: {e}")
            self.status_message.emit("标定完成")

    def _on_error(self,e):
        self.controller.set_state(self.controller.STATE_ERROR)

    def closeEvent(self, event):
        self.config_service.save()                    # ① 保存配置
        if self._live_active:
            self._stop_live_view()                # ② 停预览
        if self.controller.cancel() is not None:
            self.controller.cancel()           # ③ 请求后台尽早退出
        if self.plc is not None:
            self._disconnect_plc()
        from camera.camera_adapter import HikCamera
        HikCamera.shutdown()
        if self.thread is not None:
            self.thread.quit()
            if not self.thread.wait(3000):
                self.thread.terminate()           # 超时才强退
                self.thread.wait()
        event.accept()


    def _show_image(self, img, title: str):
        self.image_widget.show_frame(img)
        self._log(f"图像区显示: {title}")

    def _config_path(self) -> str:
        return os.path.join(PROJECT_ROOT, "gui", "config.json")


