# -*- coding: utf-8 -*-
"""应用状态机控制器：管理运行状态、控件锁定与取消请求。"""

import logging
import threading


logger = logging.getLogger(__name__)


class AppController:
    """应用状态机：IDLE / RUNNING / DONE / ERROR。"""

    STATE_IDLE = "idle"
    STATE_RUNNING = "running"
    STATE_DONE = "done"
    STATE_ERROR = "error"

    def __init__(
        self,
        widgets_to_lock,
        start_btn,
        stop_btn,
        live_btn,
        status_fn,
    ):
        self._state = self.STATE_IDLE
        self._cancel_event = None
        self._widgets = widgets_to_lock
        self._start_btn = start_btn
        self._stop_btn = stop_btn
        self._live_btn = live_btn
        self._status = status_fn
        # 进入运行状态前，记录每个参数控件原来的启用状态。
        # 例如搜索模式下：
        #   粗扫步距=True
        #   标定步距=False
        self._enabled_before_run = {}

    @property
    def state(self) -> str:
        return self._state

    @property
    def cancel_event(self):
        return self._cancel_event

    def new_cancel_event(self):
        """为每个新任务创建独立的停止事件。"""

        self._cancel_event = threading.Event()
        return self._cancel_event

    def request_cancel(self):
        """停止按钮：发送取消请求并防止重复点击。"""

        if self._cancel_event is None:
            return

        self._cancel_event.set()
        logger.info("已发送停止请求，等待当前阶段安全结束")
        self._stop_btn.setEnabled(False)

    def cancel(self):
        """静默发送取消请求，供 closeEvent 使用。"""

        if self._cancel_event is not None:
            self._cancel_event.set()

    def set_state(self, state: str):
        """切换状态，并同步相关控件的可用性。"""

        previous_state = self._state
        was_running = (
                previous_state
                == self.STATE_RUNNING
        )
        running = (
                state
                == self.STATE_RUNNING
        )

        self._state = state

        if running:
            # 只有第一次进入 RUNNING 时才保存状态。
            #
            # 如果重复调用 set_state(RUNNING)，
            # 不能把已经全部禁用的状态覆盖进快照。
            if not was_running:
                self._enabled_before_run = {
                    widget: widget.isEnabled()
                    for widget in self._widgets
                }

            # 任务运行时全部参数禁止修改。
            for widget in self._widgets:
                widget.setEnabled(False)

        elif was_running:
            # 从 RUNNING 离开时，恢复每个控件运行前的状态。
            for widget in self._widgets:
                enabled = self._enabled_before_run.get(
                    widget,
                    True,
                )
                widget.setEnabled(enabled)

            self._enabled_before_run.clear()

        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._live_btn.setEnabled(not running)

        if running:
            self._status("运行中...")