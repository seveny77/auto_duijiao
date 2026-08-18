from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt5.QtCore import QObject
import sys

class SignalOut:
    """伪装成 stdout 的对象：write() 收到文字就发日志信号"""
    def __init__(self, signal):
        self._sig = signal

    def write(self, text: str):
        if text.strip():              # 空行忽略
            self._sig.emit(text.rstrip("\n"))
        return len(text)

    def flush(self):                  # print 会调用 flush，给个空实现
        pass

# ----------测试线程----------
class VerifyWorker(QObject):  # 工作对象（不是 QThread 本身）
    log = pyqtSignal(str)  # ★ 信号：向界面发日志
    preview = pyqtSignal(object, str,int, float,) #  扫描过程预览信号： image, phase, sequence, score
    finished = pyqtSignal(object)  # ★ 信号：流程结束，发结果
    error = pyqtSignal(str)  # ★ 信号：出错
    start = pyqtSignal(object)  # 触发信号

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)  # 内部连接放类里

    @pyqtSlot(object)
    def run(self,config):  # 在后台线程里执行
        old_out = sys.stdout
        sys.stdout = SignalOut(self.log) #重新定义输出格式

        # 把 Qt 信号的 emit 方法作为普通回调注入后端配置。
        #
        # 后端只知道它可以调用：
        # config.preview_callback(image, phase, sequence, score)
        #
        # 后端不需要导入 PyQt，也不需要知道 MainWindow。
        config.preview_callback = self.preview.emit
        try:
            from verify_ncc_full import run_calibrate, run_search
            if config.action == "calibrate":
                rc = run_calibrate(config)
            else:
                rc = run_search(config)
            self.finished.emit(rc)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            # 后台任务结束后解除对 Qt 信号的引用。
            config.preview_callback = None
            sys.stdout = old_out