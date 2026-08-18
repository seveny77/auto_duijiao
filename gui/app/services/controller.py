# -*- coding: utf-8 -*-
"""应用状态机控制器：管理运行状态、控件锁定与取消请求。"""

import threading


class AppController:
    """应用状态机：IDLE / RUNNING / DONE / ERROR。

    依赖注入：需要锁定的控件、按钮、日志函数和状态栏函数由外部传入。
    """

    STATE_IDLE = "idle"
    STATE_RUNNING = "running"
    STATE_DONE = "done"
    STATE_ERROR = "error"

    def __init__(self, widgets_to_lock, start_btn, stop_btn, live_btn,
                 log_fn, status_fn):
        self._state = self.STATE_IDLE
        self._cancel_event = None
        self._widgets = widgets_to_lock
        self._start_btn = start_btn
        self._stop_btn = stop_btn
        self._live_btn = live_btn
        self._log = log_fn
        self._status = status_fn

    @property
    def state(self) -> str:
        return self._state

    @property
    def cancel_event(self):
        return self._cancel_event

    def new_cancel_event(self):
        """每次运行新建停止标记，返回给调用方塞进 params。"""
        self._cancel_event = threading.Event()
        return self._cancel_event

    def request_cancel(self):
        """停止按钮：置位 + 日志 + 禁用停止按钮（防重复点击）。"""
        if self._cancel_event is not None:
            self._cancel_event.set()
            self._log("已发送停止请求，等待当前阶段结束...")
            self._stop_btn.setEnabled(False)

    def cancel(self):
        """静默置位（closeEvent 用，不打扰日志）。"""
        if self._cancel_event is not None:
            self._cancel_event.set()

    def set_state(self, state: str):
        self._state = state
        running = state == self.STATE_RUNNING
        for w in self._widgets:
            w.setEnabled(not running)
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._live_btn.setEnabled(not running)
        if running:
            self._status("运行中...")