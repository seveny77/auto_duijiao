# -*- coding: utf-8 -*-
"""PLC 连接工作对象：在后台执行一次阻塞连接任务。"""

import logging
import threading

from PyQt5.QtCore import QObject, pyqtSignal


logger = logging.getLogger(__name__)


class PlcConnectWorker(QObject):
    """执行一次 PLC 连接和行程读取，不负责管理 GUI 状态。"""

    connected = pyqtSignal(object)
    failed = pyqtSignal(str)
    settled = pyqtSignal()

    def __init__(
        self,
        host: str,
        port: int,
        stop_event: threading.Event = None,
    ):
        super().__init__()

        self._host = host
        self._port = port
        self._stop_event = stop_event or threading.Event()

        # Worker 暂时持有已经连接成功、尚未交给 PlcService 的客户端。
        self._client = None
        self._client_lock = threading.Lock()

    def run(self):
        """执行一次连接任务。"""

        plc = None

        try:
            if self._stop_event.is_set():
                return

            from plc.client import PlcClient

            plc = PlcClient(
                self._host,
                self._port,
                timeout=3.0,
            )
            plc.connect()
            stroke = plc.read_stroke_range()

            # connect() 是阻塞操作，结束时程序可能已经请求关闭。
            if self._stop_event.is_set():
                plc.disconnect()
                logger.info("PLC 连接完成时程序已请求关闭，本次连接已释放")
                return

            # Worker 先暂时保管连接，等待 PlcService 正式接收。
            with self._client_lock:
                self._client = plc

            self.connected.emit((plc, stroke))

            # 客户端已经转移到 self._client，由 Worker 暂时保管。
            plc = None

        except Exception as exc:
            if plc is not None:
                try:
                    plc.disconnect()
                except Exception:
                    logger.exception("PLC 连接失败后的清理异常")

            # 如果程序正在主动关闭，就不再显示连接失败。
            if not self._stop_event.is_set():
                self.failed.emit(str(exc))

        finally:
            self.settled.emit()

    def take_client(self, expected=None):
        """把连接成功的客户端交给 PlcService。

        返回客户端后，Worker 不再持有它。
        expected 用于确认取走的是信号中携带的同一个客户端。
        """

        with self._client_lock:
            client = self._client

            if expected is not None and client is not expected:
                return None

            self._client = None
            return client

    def close_unclaimed_client(self):
        """关闭尚未被 PlcService 接管的客户端。"""

        client = self.take_client()

        if client is None:
            return

        try:
            client.disconnect()
        except Exception:
            logger.exception("关闭未接管的 PLC 连接时发生异常")
