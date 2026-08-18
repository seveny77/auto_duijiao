import queue
import threading
import os
import cv2
from typing import Dict, List, Optional, Tuple
import time

def save_jpg(image, path: str):
    cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(path)

def save_phase_images(collector, count: int, positions: list, prefix: str, out_dir: str):
    """把一阶段飞拍的全部帧存为 jpg：<prefix>_<序号>_<位置>um.jpg。"""
    os.makedirs(out_dir, exist_ok=True)
    saved = 0
    for i in range(count):
        img = collector.image(i)
        if img is None:
            continue
        save_jpg(img, os.path.join(out_dir, f"{prefix}_{i:04d}_{positions[i]:.0f}um.jpg"))
        saved += 1
    print(f"[保存] {prefix}: {saved}/{count} 张 -> {out_dir}")

class PhaseCollector:
    def __init__(self,
                 camera,
                 evaluator,
                 save_dir=None,
                 start_index=0,
                 max_queue=32,
                 save_all=False,
                 cancel_event=None,
                 keep_images: bool = True,
                 preview_callback=None,
                 preview_interval_s: float = 0.1,
                 phase_name: str = "",):
        self._cam = camera
        self._eval = evaluator
        self._save_dir = save_dir
        self._start_index = start_index #文件名起始编号，避免不同阶段重名
        self._save_all = save_all
        self._queue = queue.Queue(maxsize=max_queue) # 线程安全的 FIFO 队列
        self._stop = threading.Event()
        self._enqueued = 0 # 已入队计数，同时下一帧的序号
        self._processed = 0 #已完成评价的张数
        self._scores: Dict[int, float] = {} # 分数表 {帧序号: 清晰度分数}
        self._images: Dict[int, object] = {} # 图像表 {帧序号: 图像数组}
        self._error = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = cancel_event
        self._keep_images = keep_images #存图
        # GUI 模式下通常指向 VerifyWorker.preview.emit；
        # CLI 模式下保持 None。
        self._preview_callback = preview_callback
        # 预览最小时间间隔。
        #
        # max(0.0, ...) 防止出现负数配置。
        self._preview_interval_s = max(
            0.0,
            float(preview_interval_s),
        )
        # 当前采集阶段名称：
        # calibrate / coarse / fine
        self._phase_name = phase_name

        # 上一次成功发送预览的时间。
        # 0.0 表示尚未发送过，因此第一帧可以立即发送。
        self._last_preview_ts = 0.0

        if save_dir:
            os.makedirs(save_dir, exist_ok=True)

    def start(self):
        self._cam.set_trigger_mode("hardware")
        self._cam.register_frame_callback(self._on_frame)
        self._cam.start_grabbing()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self._cam.stop_grabbing()
        except Exception as e:
            print(f"[警告] 停止取流失败: {e}")

    @property
    def processed(self) -> int:
        with self._lock:
            return self._processed

    @property
    def error(self):
        with self._lock:
            return self._error

    def wait(self, count: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._cancel is not None and self._cancel.is_set():
                    return False  # 被取消：按"超时"返回 False
                if self._error is not None:
                    return False
                if self._processed >= count:
                    return True
            time.sleep(0.02)
        return False

    def queue_empty(self) -> bool:
        return self._queue.empty()

    def scores(self) -> Dict[int, float]:
        with self._lock:
            return dict(self._scores)

    def image(self, seq: int):
        with self._lock:
            return self._images.get(seq)
    #相机回调线程
    def _on_frame(self, img):
        seq = self._enqueued
        self._enqueued += 1
        try:
            self._queue.put_nowait((img, seq)) # put_nowait 非阻塞入队
        except queue.Full:
            pass

    def _emit_preview(
            self,
            img,
            seq: int,
            score: float,
    ):
        """按时间间隔发送一张过程预览图。"""

        callback = self._preview_callback

        # CLI 或无预览模式下，不做任何事情。
        if callback is None:
            return

        now = time.monotonic()

        # interval > 0 时执行限频。
        #
        # 第一帧的 _last_preview_ts 是 0，
        # 所以第一张图可以立即发送。
        if (
                self._preview_interval_s > 0
                and self._last_preview_ts > 0
                and now - self._last_preview_ts
                < self._preview_interval_s
        ):
            return

        # 先更新时间，再调用外部回调。
        #
        # 即使回调发生异常，也不会在下一帧立即频繁重试。
        self._last_preview_ts = now

        try:
            callback(
                img,
                self._phase_name,
                int(seq),
                float(score),
            )

        except Exception as e:
            # 预览只是辅助功能。
            #
            # 预览失败不能让标定、粗扫或精扫失败，
            # 所以这里只记录警告，不设置 self._error。
            print(f"[警告] 过程预览发送失败: {e}")

    def _worker(self):
        while not self._stop.is_set() or not self._queue.empty():
            if self._cancel is not None and self._cancel.is_set():
                self._stop.set()  # 提前结束取帧循环
                break
            try:
                img, seq = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                score = self._eval.evaluate_image(img, None)  # 全图评价
                with self._lock:
                    self._scores[seq] = score
                    if self._keep_images:
                        self._images[seq] = img
                    self._processed += 1
                # 锁外发送预览。
                #
                # 这里只会快速调用 Qt 信号的 emit，
                # 不直接操作 GUI。
                self._emit_preview(
                    img,
                    seq,
                    score,
                )
                if self._save_all and self._save_dir:
                    save_jpg(
                        img,
                        os.path.join(self._save_dir, f"img_{self._start_index + seq:04d}.jpg"),
                    )
            except Exception as e:
                with self._lock:
                    self._error = f"第 {seq} 帧评价失败: {e}"
                self._stop.set()