# -*- coding: utf-8 -*-
"""M60+E4O4连接、复位、使能和回零生命周期服务。"""

import logging
import threading

from PyQt5.QtCore import QObject, Qt, pyqtSignal

from gui.app.workers.motion_command_worker import MotionCommandWorker
from gui.app.workers.motion_connect_worker import MotionConnectWorker
from motion.state import MotionState


logger = logging.getLogger(__name__)


class MotionService(QObject):
    """串行管理运动后端和所有维护命令。"""

    settled = pyqtSignal()
    state_changed = pyqtSignal(object)

    def __init__(
        self,
        connect_btn,
        stroke_label,
        status_fn,
        reset_btn=None,
        servo_btn=None,
        home_btn=None,
        stop_btn=None,
        connection_label=None,
        servo_label=None,
        home_label=None,
        axis_label=None,
        position_label=None,
    ):
        super().__init__()
        self._connect_btn = connect_btn
        self._stroke_label = stroke_label
        self._status_fn = status_fn
        self._reset_btn = reset_btn
        self._servo_btn = servo_btn
        self._home_btn = home_btn
        self._stop_btn = stop_btn
        self._connection_label = connection_label
        self._servo_label = servo_label
        self._home_label = home_label
        self._axis_label = axis_label
        self._position_label = position_label

        self._backend = None
        self._stroke_range = None
        self._state = MotionState()
        self._config = None
        self._connect_worker = None
        self._connect_thread = None
        self._command_worker = None
        self._command_thread = None
        self._command_name = ""
        self._operation_cancel_event = threading.Event()
        self._shutdown_requested = threading.Event()
        self._apply_state(self._state)

    @property
    def backend(self):
        return self._backend

    @property
    def stroke_range(self):
        return self._stroke_range

    @property
    def state(self) -> MotionState:
        return self._state

    @property
    def config(self):
        return self._config

    @property
    def is_connecting(self) -> bool:
        return self._connect_thread is not None and self._connect_thread.is_alive()

    @property
    def is_busy(self) -> bool:
        return self.is_connecting or (
            self._command_thread is not None
            and self._command_thread.is_alive()
        )

    @property
    def ready_for_autofocus(self) -> bool:
        return self._state.ready_for_autofocus

    def toggle(self, config):
        if self._backend is not None:
            self.disconnect()
        else:
            self.connect(config)

    def connect(self, config):
        if self.is_busy:
            logger.warning("运动控制器正在执行操作")
            return
        self._config = config
        self._shutdown_requested.clear()
        self._set_busy_ui(True)
        self._connect_btn.setText("连接中...")
        self._status_fn("正在连接运动控制器")
        logger.info("正在连接M60和E4O4...")

        worker = MotionConnectWorker(config, self._shutdown_requested)
        worker.connected.connect(self._on_connected, Qt.QueuedConnection)
        worker.failed.connect(self._on_connect_failed, Qt.QueuedConnection)
        worker.settled.connect(self._on_connect_settled, Qt.QueuedConnection)
        thread = threading.Thread(
            target=worker.run,
            daemon=True,
            name="motion-connect",
        )
        self._connect_worker = worker
        self._connect_thread = thread
        thread.start()

    def disconnect(self):
        if self._backend is None or self.is_busy:
            return
        backend = self._backend

        def command():
            backend.disconnect()
            return MotionState()

        self._start_command("断开运动控制器", command)

    def clear_alarm(self):
        if self._backend is not None:
            self._start_command("复位报警", self._backend.clear_alarm)

    def toggle_servo(self):
        if self._backend is None:
            return
        command = (
            self._backend.servo_off
            if self._state.servo_enabled
            else self._backend.servo_on
        )
        name = "伺服去使能" if self._state.servo_enabled else "伺服使能"
        self._start_command(name, command)

    def home(self):
        if self._backend is None:
            return
        self._operation_cancel_event.clear()
        self._start_command(
            "回原点",
            lambda: self._backend.home(
                cancel_event=self._operation_cancel_event,
            ),
        )

    def stop_motion(self):
        self._operation_cancel_event.set()
        if self._backend is not None:
            self._backend.cancel_current_motion()
        self._status_fn("已请求停止当前运动")
        logger.warning("已发送运动停止请求")

    def refresh_state(self):
        if self._backend is not None:
            self._start_command("刷新运动状态", self._backend.get_state)

    def _start_command(self, name: str, command):
        if self.is_busy:
            logger.warning("运动控制器忙，无法执行: %s", name)
            return
        worker = MotionCommandWorker(self._backend, command)
        worker.succeeded.connect(self._on_command_succeeded, Qt.QueuedConnection)
        worker.failed.connect(self._on_command_failed, Qt.QueuedConnection)
        worker.settled.connect(self._on_command_settled, Qt.QueuedConnection)
        thread = threading.Thread(
            target=worker.run,
            daemon=True,
            name="motion-command",
        )
        self._command_name = name
        self._command_worker = worker
        self._command_thread = thread
        self._set_busy_ui(True)
        self._status_fn(f"正在{name}")
        logger.info("开始%s", name)
        thread.start()

    def _on_connected(self, payload):
        worker = self.sender()
        backend, stroke, state = payload
        if worker is not self._connect_worker:
            worker.close_unclaimed_backend()
            return
        if self._shutdown_requested.is_set():
            worker.close_unclaimed_backend()
            return
        claimed = worker.take_backend(expected=backend)
        if claimed is None:
            logger.error("运动后端交接失败")
            backend.disconnect()
            return
        self._backend = claimed
        self._stroke_range = stroke
        self._apply_state(state)
        self._status_fn("运动控制器已连接，请先回原点")
        logger.info("运动控制器已连接，行程: %s ~ %s µm", *stroke)

    def _on_connect_failed(self, message: str):
        if self.sender() is not self._connect_worker:
            return
        if not self._shutdown_requested.is_set():
            self._show_disconnected_state()
            self._status_fn("运动控制器连接失败")
            logger.error("运动控制器连接失败: %s", message)

    def _on_connect_settled(self):
        worker = self.sender()
        if worker is not self._connect_worker:
            worker.close_unclaimed_backend()
            return
        worker.close_unclaimed_backend()
        self._connect_worker = None
        self._connect_thread = None
        self._set_busy_ui(False)
        self.settled.emit()

    def _on_command_succeeded(self, state):
        if self.sender() is not self._command_worker:
            return
        name = self._command_name
        if name == "断开运动控制器":
            self._backend = None
            self._stroke_range = None
            self._config = None
        self._apply_state(state or MotionState())
        self._status_fn(f"{name}完成")
        logger.info("%s完成", name)

    def _on_command_failed(self, message: str, state):
        if self.sender() is not self._command_worker:
            return
        if state is not None:
            self._apply_state(state)
        self._status_fn(f"{self._command_name}失败")
        logger.error("%s失败: %s", self._command_name, message)

    def _on_command_settled(self):
        if self.sender() is not self._command_worker:
            return
        self._command_worker = None
        self._command_thread = None
        self._command_name = ""
        self._operation_cancel_event.clear()
        self._set_busy_ui(False)
        self.settled.emit()

    def _apply_state(self, state: MotionState):
        self._state = state
        if state.connected:
            self._connect_btn.setText("断开运动控制器")
            if state.stroke_min_um is not None:
                self._stroke_range = (state.stroke_min_um, state.stroke_max_um)
                self._stroke_label.setText(
                    f"{state.stroke_min_um} ~ {state.stroke_max_um} µm"
                )
        else:
            self._connect_btn.setText("连接运动控制器")
            self._stroke_label.setText("未连接")

        self._set_label(self._connection_label, "已连接" if state.connected else "未连接")
        self._set_label(self._servo_label, "已使能" if state.servo_enabled else "未使能")
        self._set_label(self._home_label, "已回零" if state.homed else "未回零")
        self._set_label(self._axis_label, state.message)
        position_text = "--" if state.position_um is None else f"{state.position_um:.2f} µm"
        self._set_label(self._position_label, position_text)

        hazard = any((
            state.alarm,
            state.emergency_stop,
            state.positive_limit,
            state.negative_limit,
            state.offline,
        ))
        if self._axis_label is not None:
            self._axis_label.setStyleSheet("color: #c62828;" if hazard else "")
        if self._servo_btn is not None:
            self._servo_btn.setText("伺服去使能" if state.servo_enabled else "伺服使能")
        self._update_button_states()
        self.state_changed.emit(state)

    def _set_busy_ui(self, busy: bool):
        self._update_button_states(busy_override=busy)

    def _update_button_states(self, busy_override=None):
        busy = self.is_busy if busy_override is None else busy_override
        connected = self._state.connected
        safe = connected and not any((
            self._state.alarm,
            self._state.emergency_stop,
            self._state.positive_limit,
            self._state.negative_limit,
            self._state.offline,
            self._state.moving,
        ))
        self._connect_btn.setEnabled(not busy)
        self._set_enabled(self._reset_btn, connected and not busy)
        self._set_enabled(self._servo_btn, safe and not busy)
        self._set_enabled(self._home_btn, safe and not busy)
        self._set_enabled(self._stop_btn, connected and busy)

    @staticmethod
    def _set_label(label, text: str):
        if label is not None:
            label.setText(text)

    @staticmethod
    def _set_enabled(widget, enabled: bool):
        if widget is not None:
            widget.setEnabled(enabled)

    def shutdown(self, timeout_s: float = 8.0) -> bool:
        self._shutdown_requested.set()
        self.stop_motion()
        for thread in (self._connect_thread, self._command_thread):
            if thread is not None:
                thread.join(timeout=timeout_s)
                if thread.is_alive():
                    logger.warning("运动控制线程未在限时内退出")
                    return False
        if self._connect_worker is not None:
            self._connect_worker.close_unclaimed_backend()
        if self._backend is not None:
            try:
                self._backend.disconnect()
            except Exception:
                logger.exception("关闭运动后端失败")
        self._connect_worker = self._connect_thread = None
        self._command_worker = self._command_thread = None
        self._backend = None
        self._stroke_range = None
        self._config = None
        self._show_disconnected_state()
        return True

    def _show_disconnected_state(self):
        self._stroke_range = None
        self._apply_state(MotionState())
