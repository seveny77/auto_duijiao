# frame_worker.py
import logging
import queue
import threading


logger = logging.getLogger(__name__)

class FrameWorker:
    def __init__(self, camera, process_fn, max_queue=16):
        self._cam = camera
        self._process = process_fn
        self._queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._count = [0]

    def start(self, trigger_mode="off"):
        """启动：设置触发 → 注册回调 → camera开始取流"""
        self._cam.set_trigger_mode(trigger_mode)
        self._cam.register_frame_callback(self._on_frame)
        self._cam.start_grabbing()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        """停止：等队列排空 → 停取流"""
        self._stop.set()
        self._thread.join(timeout=5)
        self._cam.stop_grabbing()

    @property
    def processed_count(self):
        return self._count[0]

    # -------- 内部 --------
    def _on_frame(self, img):
        """SDK回调：只入队"""
        try:
            self._queue.put_nowait((img, self._count[0]))
        except queue.Full:
            pass

    def _worker(self):
        """工作线程：从队列取 → 调用用户处理函数"""
        while not self._stop.is_set() or not self._queue.empty():
            try:
                img, _ = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._process(
                    img,
                    self._count[0],
                )

            except Exception:
                logger.exception(
                    "FrameWorker 第 %d 帧处理失败",
                    self._count[0],
                )

            self._count[0] += 1
