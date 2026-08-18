"""PLC 连接工作对象：把阻塞的 TCP 连接放到后台线程，结果用信号回传。"""

from PyQt5.QtCore import QObject, pyqtSignal

class PlcConnectWorker(QObject):
    connected = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, host: str, port: int):
        super().__init__()
        self._host = host
        self._port = port

    def run(self):
        plc = None
        try:
            from plc.client import PlcClient
            plc = PlcClient(self._host, self._port, timeout=3.0)
            plc.connect()
            stroke = plc.read_stroke_range()  # (min_um, max_um)
            self.connected.emit((plc, stroke))
        except Exception as e:
            if plc is not None:
                try:
                    plc.disconnect()
                except Exception:
                    pass
            self.failed.emit(str(e))
