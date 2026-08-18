"""日志面板：带时间戳追加 + 自动滚动。"""

import time
from PyQt5.QtWidgets import QGroupBox, QVBoxLayout, QPlainTextEdit

class LogPanel(QGroupBox):
    """底部日志区。"""

    def __init__(self, parent=None):
        super().__init__("日志", parent)
        layout = QVBoxLayout(self)
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setPlaceholderText("日志输出区")
        layout.addWidget(self._view)
        self.setFixedHeight(180)          # 固定高度，保持原布局

    def append(self, text: str):
        """追加一行带时间戳的日志并滚到底部。"""
        ts = time.strftime("%H:%M:%S", time.localtime())
        self._view.appendPlainText(f"[{ts}] {text}")
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())