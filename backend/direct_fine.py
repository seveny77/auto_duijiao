# -*- coding: utf-8 -*-
"""单程精扫采集：第一帧检测ROI，所有帧按同一ROI评价。"""

from dataclasses import dataclass, field
import logging
import os
import queue
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from backend.collector import save_jpg


logger = logging.getLogger(__name__)

LocalRoi = Tuple[int, int, int, int]
RawBox = Optional[Tuple[float, float, float, float]]
RoiDetector = Callable[[object], Tuple[LocalRoi, str, RawBox]]


@dataclass(frozen=True)
class DirectFineCollectionResult:
    """一次单程精扫完成后的图像评价结果。"""

    expected_count: int
    received_count: int
    enqueued_count: int
    dropped_count: int
    processed_count: int
    scores: Dict[int, float]
    best_index: int
    best_score: float
    best_image: object
    evaluation_roi_local: LocalRoi
    roi_source: str
    detect_box_local: RawBox
    timings_ms: Dict[str, float] = field(default_factory=dict)


class DirectFineCollector:
    """为一次连续精扫服务的相机回调与后台评价线程。"""

    def __init__(
        self,
        camera,
        evaluator,
        roi_detector: RoiDetector,
        expected_count: int,
        *,
        cancel_event=None,
        max_queue: Optional[int] = None,
        save_all: bool = False,
        save_dir: Optional[str] = None,
    ):
        if expected_count <= 0:
            raise ValueError(
                f"单程精扫理论帧数必须大于0: {expected_count}"
            )

        self._cam = camera
        self._evaluator = evaluator
        self._roi_detector = roi_detector
        self._expected_count = int(expected_count)
        self._cancel = cancel_event
        self._save_all = bool(save_all)
        self._save_dir = save_dir
        queue_size = (
            max(32, self._expected_count + 2)
            if max_queue is None
            else int(max_queue)
        )
        if queue_size <= 0:
            raise ValueError(f"评价队列容量必须大于0: {queue_size}")
        self._queue = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        self._received = 0
        self._enqueued = 0
        self._dropped = 0
        self._processed = 0
        self._scores: Dict[int, float] = {}
        self._best_index = -1
        self._best_score = float("-inf")
        self._best_image = None
        self._evaluation_roi_local: Optional[LocalRoi] = None
        self._roi_source = ""
        self._detect_box_local: RawBox = None
        self._error: Optional[str] = None

        self._start_ts = 0.0
        self._first_frame_ts = 0.0
        self._last_processed_ts = 0.0
        self._yolo_ms = 0.0
        self._score_ms = 0.0

        if self._save_all and self._save_dir:
            os.makedirs(self._save_dir, exist_ok=True)

    def start(self) -> None:
        """注册相机回调并启动硬件触发采集。"""

        self._start_ts = time.perf_counter()
        self._cam.set_trigger_mode("hardware")
        self._cam.register_frame_callback(self._on_frame)
        self._thread = threading.Thread(
            target=self._worker,
            name="direct-fine-evaluator",
            daemon=True,
        )
        self._thread.start()
        try:
            self._cam.start_grabbing()
        except Exception:
            self._stop.set()
            self._thread.join(timeout=2.0)
            self._thread = None
            raise

    def stop(self, timeout: float = 5.0) -> bool:
        """停止取流，并等待已入队图像处理完成。"""

        self._stop.set()
        try:
            self._cam.stop_grabbing()
        except Exception as error:
            logger.warning("单程精扫停止取流失败: %s", error)

        thread = self._thread
        if thread is not None:
            if thread is threading.current_thread():
                logger.error("DirectFineCollector不能等待自身退出")
                return False
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.error(
                    "DirectFineCollector在%.1fs内没有退出",
                    timeout,
                )
                return False
        self._thread = None
        return True

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def stats(self) -> dict:
        with self._lock:
            return {
                "received": self._received,
                "enqueued": self._enqueued,
                "dropped": self._dropped,
                "processed": self._processed,
            }

    def wait(self, timeout: float) -> bool:
        """等待理论帧数全部完成评价。"""

        if timeout <= 0:
            raise ValueError(f"帧处理等待时间必须大于0: {timeout}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._cancel is not None and self._cancel.is_set():
                    return False
                if self._error is not None or self._dropped > 0:
                    return False
                if self._processed >= self._expected_count:
                    return True
            time.sleep(0.01)
        return False

    def result(self) -> DirectFineCollectionResult:
        """在完整处理理论帧数后生成不可变结果快照。"""

        with self._lock:
            if self._error is not None:
                raise RuntimeError(self._error)
            if self._dropped:
                raise RuntimeError(
                    f"单程精扫评价队列丢帧: {self._dropped}"
                )
            if self._processed != self._expected_count:
                raise RuntimeError(
                    "单程精扫帧数不完整: "
                    f"expected={self._expected_count}, "
                    f"processed={self._processed}"
                )
            if self._best_index < 0 or self._best_image is None:
                raise RuntimeError("单程精扫没有产生最佳图像")
            if self._evaluation_roi_local is None:
                raise RuntimeError("单程精扫没有生成清晰度评价ROI")

            first_wait_ms = 0.0
            process_elapsed_ms = 0.0
            if self._first_frame_ts > 0 and self._start_ts > 0:
                first_wait_ms = (
                    self._first_frame_ts - self._start_ts
                ) * 1000
            if self._last_processed_ts > 0 and self._first_frame_ts > 0:
                process_elapsed_ms = (
                    self._last_processed_ts - self._first_frame_ts
                ) * 1000

            return DirectFineCollectionResult(
                expected_count=self._expected_count,
                received_count=self._received,
                enqueued_count=self._enqueued,
                dropped_count=self._dropped,
                processed_count=self._processed,
                scores=dict(self._scores),
                best_index=self._best_index,
                best_score=self._best_score,
                best_image=self._best_image,
                evaluation_roi_local=self._evaluation_roi_local,
                roi_source=self._roi_source,
                detect_box_local=self._detect_box_local,
                timings_ms={
                    "first_frame_wait_ms": first_wait_ms,
                    "yolo_ms": self._yolo_ms,
                    "score_total_ms": self._score_ms,
                    "score_avg_ms": (
                        self._score_ms / self._processed
                        if self._processed
                        else 0.0
                    ),
                    "frame_processing_elapsed_ms": process_elapsed_ms,
                },
            )

    def _on_frame(self, image) -> None:
        if self._stop.is_set():
            return

        now = time.perf_counter()
        with self._lock:
            sequence = self._received
            self._received += 1
            if self._first_frame_ts == 0.0:
                self._first_frame_ts = now

        try:
            self._queue.put_nowait((image, sequence))
        except queue.Full:
            with self._lock:
                self._dropped += 1
        else:
            with self._lock:
                self._enqueued += 1

    def _worker(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            if self._cancel is not None and self._cancel.is_set():
                self._stop.set()
                break
            try:
                image, sequence = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue

            try:
                if self._evaluation_roi_local is None:
                    yolo_t0 = time.perf_counter()
                    roi, source, raw_box = self._roi_detector(image)
                    yolo_ms = (time.perf_counter() - yolo_t0) * 1000
                    with self._lock:
                        self._evaluation_roi_local = roi
                        self._roi_source = source
                        self._detect_box_local = raw_box
                        self._yolo_ms = yolo_ms

                score_t0 = time.perf_counter()
                score = float(
                    self._evaluator.evaluate_image(
                        image,
                        self._evaluation_roi_local,
                    )
                )
                score_ms = (time.perf_counter() - score_t0) * 1000

                if self._save_all and self._save_dir:
                    save_jpg(
                        image,
                        os.path.join(
                            self._save_dir,
                            f"direct_{sequence:04d}.jpg",
                        ),
                    )

                with self._lock:
                    self._scores[sequence] = score
                    self._score_ms += score_ms
                    if score > self._best_score:
                        self._best_score = score
                        self._best_index = sequence
                        self._best_image = image.copy()
                    self._processed += 1
                    self._last_processed_ts = time.perf_counter()

            except Exception as error:
                with self._lock:
                    self._error = (
                        f"单程精扫第{sequence}帧处理失败: {error}"
                    )
                self._stop.set()
            finally:
                self._queue.task_done()
