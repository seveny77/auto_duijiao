# -*- coding: utf-8 -*-
"""把 Python 标准 logging 安全地转发到 PyQt 日志窗口。"""

import logging

from PyQt5.QtCore import (
    QObject,
    Qt,
    pyqtSignal,
)


class _QtLogEmitter(QObject):
    """logging 工作线程与 GUI 主线程之间的信号桥。"""

    # message 信号只负责传递最终格式化好的日志文字。
    message = pyqtSignal(str)


class GuiLogFormatter(logging.Formatter):
    """把标准日志记录格式化成适合 GUI 显示的中文文本。"""

    # 标准 logging 级别和 GUI 中文前缀之间的对应关系。
    LEVEL_PREFIX = {
        logging.DEBUG: "[调试] ",
        logging.WARNING: "[警告] ",
        logging.ERROR: "[错误] ",
        logging.CRITICAL: "[严重] ",
    }

    def format(self, record):
        """把一条 LogRecord 转换成最终显示的字符串。"""

        # 先调用父类 Formatter。
        #
        # 父类会负责：
        #
        # 1. 处理 %(message)s；
        # 2. 把日志参数代入正文；
        # 3. 如果存在异常，追加 traceback；
        # 4. 处理多行日志。
        text = super().format(record)

        # INFO 是正常过程信息，不加额外前缀。
        #
        # WARNING、ERROR 等级别则添加中文前缀。
        prefix = self.LEVEL_PREFIX.get(
            record.levelno,
            "",
        )

        return f"{prefix}{text}"


class QtLogHandler(logging.Handler):
    """把标准 logging 日志记录安全地发送到 GUI。"""

    def __init__(
        self,
        append_fn,
        level: int = logging.INFO,
    ):
        # 初始化 logging.Handler。
        super().__init__(level=level)

        # 创建一个只负责发射 Qt 信号的 QObject。
        self._emitter = _QtLogEmitter()

        # append_fn 后面传入 MainWindow._log。
        #
        # 显式指定 Qt.QueuedConnection，保证无论日志来自：
        #
        #   VerifyWorker
        #   PhaseCollector
        #   相机 SDK 回调线程
        #   实时预览线程
        #
        # 最终的 append_fn 都在 GUI 主线程中执行。
        self._emitter.message.connect(
            append_fn,
            Qt.QueuedConnection,
        )

        # LogPanel 自己会添加时间戳，
        # 所以 GUI Formatter 只保留日志正文。
        self.setFormatter(
            GuiLogFormatter(
                "%(message)s"
            )
        )

    def emit(self, record):
        """logging 产生一条记录时调用。"""

        try:
            # self.format(record) 会调用上面设置的
            # GuiLogFormatter。
            text = self.format(record)

            # 不在当前线程中直接修改 GUI。
            #
            # 这里只发出 Qt 信号，让 Qt 把更新操作
            # 排队送到 GUI 主线程。
            self._emitter.message.emit(text)

        except Exception:
            # 如果日志 Handler 自己发生异常，
            # 使用 logging 内部机制处理。
            #
            # 这里不能再调用 logger.error()，
            # 否则可能触发：
            #
            # logger.error()
            #   → QtLogHandler.emit()
            #     → 再次出错
            #       → logger.error()
            #
            # 最后形成无限递归。
            self.handleError(record)


def install_qt_log_handler(
    append_fn,
    level: int = logging.INFO,
) -> QtLogHandler:
    """给根 logger 安装 GUI 日志 Handler。

    参数：
        append_fn:
            接收一段字符串并显示到 GUI 的函数。
            当前项目传入 MainWindow._log。

        level:
            GUI 最低显示级别，默认 INFO。

    返回：
        新创建的 QtLogHandler。
        主窗口需要保存它，以便关闭时卸载。
    """

    root_logger = logging.getLogger()

    # 根 logger 至少接收 INFO 及以上日志。
    root_logger.setLevel(level)

    handler = QtLogHandler(
        append_fn=append_fn,
        level=level,
    )

    root_logger.addHandler(handler)

    return handler


def remove_qt_log_handler(handler):
    """从根 logger 中卸载 GUI Handler。"""

    if handler is None:
        return

    root_logger = logging.getLogger()

    # 不再把后续日志发送给这个窗口。
    root_logger.removeHandler(handler)

    # 释放 Handler 自己持有的资源。
    handler.close()