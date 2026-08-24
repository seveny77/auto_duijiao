import logging

from PyQt5.QtCore import (
    QObject,
    pyqtSignal,
    pyqtSlot,
)


logger = logging.getLogger(__name__)


# ---------- 测试线程 ----------
class VerifyWorker(QObject):
    """在后台 QThread 中执行搜索或标定任务。"""

    # 扫描过程预览：
    #
    # image, phase, sequence, score
    preview = pyqtSignal(
        object,
        str,
        int,
        float,
    )

    # 后台流程正常结束，传递结果对象。
    finished = pyqtSignal(object)

    # 无论成功、失败还是取消，
    # 本次后台任务都已经执行完 finally。
    settled = pyqtSignal()

    # 后台流程出现未捕获异常，
    # 传递适合状态栏显示的简短错误。
    error = pyqtSignal(str)

    # 主线程通过这个信号触发 run()。
    start = pyqtSignal(object)

    def __init__(self):
        super().__init__()

        # start 信号到达后，在当前对象所属的
        # VerifyWorker QThread 中执行 run()。
        self.start.connect(self.run)

    @pyqtSlot(object)
    def run(self, config):
        """在后台线程中执行搜索或标定。"""

        # 把 Qt 预览信号的 emit 方法作为普通回调注入后端。
        #
        # 后端只知道它可以调用：
        #
        # config.preview_callback(
        #     image,
        #     phase,
        #     sequence,
        #     score,
        # )
        #
        # 后端不需要导入 PyQt，也不需要知道 MainWindow。
        config.preview_callback = (
            self.preview.emit
        )

        try:
            from verify_ncc_full import (
                run_calibrate,
                run_search,
            )

            if config.action == "calibrate":
                result = run_calibrate(config)
            else:
                result = run_search(config)

            self.finished.emit(result)

        except Exception as e:
            # 简短错误用于 MainWindow 的状态处理。
            exception_type = type(e).__name__
            exception_message = str(e).strip()

            if exception_message:
                short_message = (
                    f"{exception_type}: "
                    f"{exception_message}"
                )
            else:
                short_message = exception_type

            # 通知 MainWindow：
            # 后台任务出现异常，需要切换界面状态。
            self.error.emit(short_message)

            action_name = getattr(
                config,
                "action",
                "unknown",
            )

            # 自动记录当前异常的完整 traceback。
            logger.exception(
                "后台任务异常调用栈: action=%s",
                action_name,
            )

        finally:
            # 先解除后端配置对 Qt 预览信号的引用。
            config.preview_callback = None

            # 最后通知 GUI：
            # 本次后台任务已经执行完最终清理。
            self.settled.emit()