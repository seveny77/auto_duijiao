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


@dataclass(frozen=True)
class SoftwareBestFrameResult:
    """连续软件触发采集的最佳帧结果。"""

    received_count: int
    processed_count: int
    dropped_count: int
    best_index: int
    best_score: float
    best_image: object
    evaluation_roi_local: LocalRoi
    completed: bool
    timings_ms: Dict[str, float] = field(default_factory=dict)


class SoftwareBestFrameCollector:
    """通过软件触发连续采图，只保留清晰度最高的一帧。

    采集器不依赖运动轴。外部开始运动后调用 :meth:`start`，
    运动结束时调用 :meth:`stop`；每处理完一帧，Worker 才发送下一次
    软件触发，避免相机缓存被一次性塞满。
    """

    def __init__(
        self,
        camera,
        evaluator,
        evaluation_roi: Optional[LocalRoi] = None,
        *,
        cancel_event=None,
        max_queue: int = 2,
        trigger_interval_s: float = 0.0,
        frame_timeout_s: float = 1.0,
        frame_limit: Optional[int] = None,
        preview_callback=None,
    ):
        if max_queue <= 0:
            raise ValueError(f"软触发评价队列容量必须大于0: {max_queue}")
        if trigger_interval_s < 0:
            raise ValueError("软触发间隔不能小于0")
        if frame_timeout_s <= 0:
            raise ValueError("软触发回调超时必须大于0")
        if frame_limit is not None and frame_limit <= 0:
            raise ValueError("软触发帧数上限必须大于0")

        self._cam = camera
        self._evaluator = evaluator
        self._evaluation_roi = evaluation_roi
        self._cancel = cancel_event
        self._queue = queue.Queue(maxsize=int(max_queue))
        self._trigger_interval_s = float(trigger_interval_s)
        self._frame_timeout_s = float(frame_timeout_s)
        self._frame_limit = frame_limit
        self._preview_callback = preview_callback

        self._stop = threading.Event()
        self._completed = threading.Event()
        self._frame_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        self._received = 0
        self._processed = 0
        self._dropped = 0
        self._triggered = 0
        self._scores: Dict[int, float] = {}
        self._best_index = -1
        self._best_score = float("-inf")
        self._best_image = None
        self._resolved_roi: Optional[LocalRoi] = None
        self._error: Optional[str] = None
        self._start_ts = 0.0
        self._first_frame_ts = 0.0
        self._last_processed_ts = 0.0
        self._last_trigger_ts = 0.0
        self._score_ms = 0.0

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def start(self) -> None:
        """切换到软件触发、启动取流并发送第一帧触发。"""

        if self._thread is not None:
            raise RuntimeError("软件触发采集器已经启动")
        self._start_ts = time.perf_counter()
        self._cam.set_trigger_mode("software")
        self._cam.register_frame_callback(self._on_frame)
        self._thread = threading.Thread(
            target=self._worker,
            name="software-best-frame",
            daemon=True,
        )
        self._thread.start()
        try:
            self._cam.start_grabbing()
            self._send_trigger()
        except Exception:
            self._stop.set()
            try:
                self._cam.stop_grabbing()
            except Exception:
                logger.exception("软件触发采集启动失败后的停流失败")
            self._thread.join(timeout=2.0)
            self._thread = None
            raise

    def stop(self, timeout: float = 5.0) -> bool:
        """停止后续触发、停止取流并等待已入队图像处理完成。"""

        self._stop.set()
        try:
            self._cam.stop_grabbing()
        except Exception as error:
            logger.warning("软件触发采集停止取流失败: %s", error)

        thread = self._thread
        if thread is not None:
            if thread is threading.current_thread():
                return False
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.error("软件触发采集器在 %.1fs 内没有退出", timeout)
                return False
        self._thread = None
        return True

    def wait(self, timeout: float) -> bool:
        """等待有限帧采集完成；连续采集模式由外部 stop 结束。"""

        if timeout <= 0:
            raise ValueError("等待超时必须大于0")
        if self._frame_limit is None:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._completed.is_set():
                return self.error is None
            if self._cancel is not None and self._cancel.is_set():
                return False
            time.sleep(0.01)
        return False

    def result(self) -> SoftwareBestFrameResult:
        """生成当前最佳帧快照。"""

        with self._lock:
            if self._error is not None:
                raise RuntimeError(self._error)
            if self._best_index < 0 or self._best_image is None:
                raise RuntimeError("软件触发采集没有产生最佳图像")

            height, width = self._best_image.shape[:2]
            roi = self._resolved_roi or (0, 0, width, height)
            first_wait_ms = 0.0
            process_elapsed_ms = 0.0
            if self._first_frame_ts and self._start_ts:
                first_wait_ms = (
                    self._first_frame_ts - self._start_ts
                ) * 1000
            if self._last_processed_ts and self._first_frame_ts:
                process_elapsed_ms = (
                    self._last_processed_ts - self._first_frame_ts
                ) * 1000

            return SoftwareBestFrameResult(
                received_count=self._received,
                processed_count=self._processed,
                dropped_count=self._dropped,
                best_index=self._best_index,
                best_score=self._best_score,
                best_image=self._best_image,
                evaluation_roi_local=roi,
                completed=self._completed.is_set(),
                timings_ms={
                    "first_frame_wait_ms": first_wait_ms,
                    "score_total_ms": self._score_ms,
                    "score_avg_ms": (
                        self._score_ms / self._processed
                        if self._processed else 0.0
                    ),
                    "frame_processing_elapsed_ms": process_elapsed_ms,
                },
            )

    def _on_frame(self, image) -> None:
        """SDK 回调中只复制引用并入队，不计算清晰度。"""

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
                self._error = "软件触发评价队列已满，无法保证逐帧处理"
            self._stop.set()
        else:
            self._frame_event.set()

    def _worker(self) -> None:
        try:
            while not self._stop.is_set() or not self._queue.empty():
                if self._cancel is not None and self._cancel.is_set():
                    self._stop.set()
                    break
                try:
                    image, sequence = self._queue.get(timeout=0.05)
                except queue.Empty:
                    with self._lock:
                        trigger_pending = (
                            self._triggered > self._processed
                        )
                        last_trigger_ts = self._last_trigger_ts
                    if (
                        trigger_pending
                        and last_trigger_ts > 0
                        and time.monotonic() - last_trigger_ts
                        > self._frame_timeout_s
                    ):
                        with self._lock:
                            self._error = (
                                "软件触发已发送，但在 "
                                f"{self._frame_timeout_s:g}s 内没有收到图像"
                            )
                        self._stop.set()
                    continue

                try:
                    self._process_frame(image, sequence)
                except Exception as error:
                    with self._lock:
                        self._error = f"软件触发第{sequence}帧处理失败: {error}"
                    self._stop.set()
                finally:
                    self._queue.task_done()

                if self._stop.is_set():
                    continue
                if (
                    self._frame_limit is not None
                    and self._processed >= self._frame_limit
                ):
                    self._completed.set()
                    self._stop.set()
                    continue
                if self._trigger_interval_s:
                    self._stop.wait(self._trigger_interval_s)
                if not self._stop.is_set():
                    try:
                        self._send_trigger()
                    except Exception as error:
                        with self._lock:
                            self._error = f"软件触发发送失败: {error}"
                        self._stop.set()
        finally:
            try:
                self._cam.stop_grabbing()
            except Exception:
                logger.debug("软件触发采集线程退出时停流失败", exc_info=True)

    def _process_frame(self, image, sequence: int) -> None:
        if self._resolved_roi is None:
            self._resolved_roi = self._normalize_roi(image)
        score_t0 = time.perf_counter()
        score = float(
            self._evaluator.evaluate_image(image, self._resolved_roi)
        )
        score_ms = (time.perf_counter() - score_t0) * 1000
        if self._preview_callback is not None:
            self._preview_callback(image, sequence, score)
        with self._lock:
            self._scores[sequence] = score
            self._score_ms += score_ms
            if score > self._best_score:
                self._best_score = score
                self._best_index = sequence
                self._best_image = (
                    image.copy() if hasattr(image, "copy") else image
                )
            self._processed += 1
            self._last_processed_ts = time.perf_counter()

    def _normalize_roi(self, image) -> LocalRoi:
        height, width = image.shape[:2]
        if self._evaluation_roi is None:
            return 0, 0, int(width), int(height)
        values = tuple(int(value) for value in self._evaluation_roi)
        if len(values) != 4:
            raise ValueError("清晰度 ROI 必须是 (x, y, width, height)")
        x, y, roi_width, roi_height = values
        if (
            x < 0 or y < 0 or roi_width <= 0 or roi_height <= 0
            or x + roi_width > width
            or y + roi_height > height
        ):
            raise ValueError(
                f"清晰度 ROI 越界: {values}, 图像={width}x{height}"
            )
        return values

    def _send_trigger(self) -> None:
        self._cam.trigger_software()
        with self._lock:
            self._triggered += 1
            self._last_trigger_ts = time.monotonic()


@dataclass(frozen=True)
class ContinuousBestFrameResult:
    """连续自由运行采集结束后的最佳帧与运行统计。"""

    received_count: int
    enqueued_count: int
    processed_count: int
    dropped_count: int
    queue_peak: int
    best_index: int
    best_score: float
    best_image: object
    evaluation_roi_local: LocalRoi
    configured_fps: float
    resulting_fps: Optional[float]
    completed: bool
    timings_ms: Dict[str, float] = field(default_factory=dict)


class ContinuousBestFrameCollector:
    """连续采集时异步评价清晰度，仅保留一张最佳图。

    相机 SDK 回调线程只做一次非阻塞入队，绝不在回调中执行 OpenCV
    清晰度计算、GUI 刷新或文件保存。后台线程负责评价图像；只有出现新的
    最佳帧时，才通过 ``preview_callback`` 通知外部更新预览。

    本类不依赖运动轴。后续 Pipeline 会在轴开始单程扫描前调用
    :meth:`start`，扫描结束后调用 :meth:`stop_and_drain`。
    """

    def __init__(
        self,
        camera,
        evaluator,
        evaluation_roi: Optional[LocalRoi] = None,
        *,
        target_fps: float,
        max_queue: int = 4,
        first_frame_timeout_s: float = 1.0,
        drain_timeout_s: float = 5.0,
        cancel_event=None,
        preview_callback=None,
    ):
        requested_fps = float(target_fps)
        if requested_fps <= 0:
            raise ValueError("连续采集目标帧率必须大于0")
        if max_queue <= 0:
            raise ValueError("连续采集评价队列容量必须大于0")
        if first_frame_timeout_s <= 0:
            raise ValueError("连续采集首帧超时必须大于0")
        if drain_timeout_s <= 0:
            raise ValueError("连续采集排空超时必须大于0")

        self._cam = camera
        self._evaluator = evaluator
        self._evaluation_roi = evaluation_roi
        self._target_fps = requested_fps
        self._queue = queue.Queue(maxsize=int(max_queue))
        self._first_frame_timeout_s = float(first_frame_timeout_s)
        self._drain_timeout_s = float(drain_timeout_s)
        self._cancel = cancel_event
        self._preview_callback = preview_callback

        self._stop = threading.Event()
        self._first_frame_event = threading.Event()
        self._completed = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._accepting_frames = False

        self._received = 0
        self._enqueued = 0
        self._processed = 0
        self._dropped = 0
        self._queue_peak = 0
        self._best_index = -1
        self._best_score = float("-inf")
        self._best_image = None
        self._resolved_roi: Optional[LocalRoi] = None
        self._configured_fps = requested_fps
        self._resulting_fps: Optional[float] = None
        self._error: Optional[str] = None
        self._start_ts = 0.0
        self._first_frame_ts = 0.0
        self._last_processed_ts = 0.0
        self._score_ms = 0.0

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def stats(self) -> dict:
        """返回无需等待任务结束的轻量运行统计。"""

        with self._lock:
            return {
                "received": self._received,
                "enqueued": self._enqueued,
                "processed": self._processed,
                "dropped": self._dropped,
                "queue_peak": self._queue_peak,
                "configured_fps": self._configured_fps,
                "resulting_fps": self._resulting_fps,
            }

    def start(self) -> None:
        """切换自由运行，启动后台评价线程和相机连续取流。"""

        if self._thread is not None:
            raise RuntimeError("连续采集器已经启动")

        self._start_ts = time.perf_counter()

        # 连续采集必须显式关闭硬件/软件触发。帧率节点在不同相机上
        # 可能依赖 TriggerMode=OFF，因此顺序固定为先自由运行、后设帧率。
        self._cam.set_trigger_mode("off")
        frame_rate_info = self._cam.set_continuous_frame_rate(
            self._target_fps
        )
        self._configured_fps = float(
            getattr(frame_rate_info, "configured_fps", self._target_fps)
        )
        resulting_fps = getattr(frame_rate_info, "resulting_fps", None)
        self._resulting_fps = (
            float(resulting_fps)
            if resulting_fps is not None
            else None
        )

        self._cam.register_frame_callback(self._on_frame)
        self._accepting_frames = True
        self._thread = threading.Thread(
            target=self._worker,
            name="continuous-best-frame",
            daemon=True,
        )
        self._thread.start()
        try:
            self._cam.start_grabbing()
        except Exception:
            self._accepting_frames = False
            self._stop.set()
            self._thread.join(timeout=2.0)
            self._thread = None
            raise

    def wait_for_first_frame(self, timeout: Optional[float] = None) -> bool:
        """等待首帧；默认使用构造时配置的首帧超时。"""

        wait_timeout = (
            self._first_frame_timeout_s
            if timeout is None
            else float(timeout)
        )
        if wait_timeout <= 0:
            raise ValueError("首帧等待时间必须大于0")
        if not self._first_frame_event.wait(wait_timeout):
            return False
        return self.error is None

    def stop_and_drain(self, timeout: Optional[float] = None) -> bool:
        """停止连续取流，并等待已入队帧全部完成评价。"""

        drain_timeout = (
            self._drain_timeout_s if timeout is None else float(timeout)
        )
        if drain_timeout <= 0:
            raise ValueError("连续采集排空超时必须大于0")

        self._accepting_frames = False
        self._stop.set()
        try:
            self._cam.stop_grabbing()
        except Exception as error:
            logger.warning("连续采集停止取流失败: %s", error)

        thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            raise RuntimeError("连续采集器不能在评价线程内等待自身退出")
        thread.join(timeout=drain_timeout)
        if thread.is_alive():
            with self._lock:
                if self._error is None:
                    self._error = (
                        "连续采集排空超时: "
                        f"{drain_timeout:g}s 内未完成队列处理"
                    )
            return False

        self._thread = None
        self._completed.set()
        return self.error is None

    def result(self) -> ContinuousBestFrameResult:
        """取得当前已处理完成的最佳帧结果快照。"""

        with self._lock:
            if self._error is not None:
                raise RuntimeError(self._error)
            if self._dropped:
                raise RuntimeError(
                    f"连续采集评价队列已满，已拒绝{self._dropped}帧"
                )
            if self._best_index < 0 or self._best_image is None:
                raise RuntimeError("连续采集没有产生最佳图像")

            height, width = self._best_image.shape[:2]
            roi = self._resolved_roi or (0, 0, int(width), int(height))
            first_wait_ms = 0.0
            processing_elapsed_ms = 0.0
            capture_elapsed_ms = 0.0
            if self._first_frame_ts and self._start_ts:
                first_wait_ms = (
                    self._first_frame_ts - self._start_ts
                ) * 1000
            if self._last_processed_ts and self._first_frame_ts:
                processing_elapsed_ms = (
                    self._last_processed_ts - self._first_frame_ts
                ) * 1000
            if self._last_processed_ts and self._start_ts:
                capture_elapsed_ms = (
                    self._last_processed_ts - self._start_ts
                ) * 1000

            return ContinuousBestFrameResult(
                received_count=self._received,
                enqueued_count=self._enqueued,
                processed_count=self._processed,
                dropped_count=self._dropped,
                queue_peak=self._queue_peak,
                best_index=self._best_index,
                best_score=self._best_score,
                best_image=self._best_image,
                evaluation_roi_local=roi,
                configured_fps=self._configured_fps,
                resulting_fps=self._resulting_fps,
                completed=self._completed.is_set(),
                timings_ms={
                    "first_frame_wait_ms": first_wait_ms,
                    "score_total_ms": self._score_ms,
                    "score_avg_ms": (
                        self._score_ms / self._processed
                        if self._processed else 0.0
                    ),
                    "frame_processing_elapsed_ms": processing_elapsed_ms,
                    "capture_and_processing_elapsed_ms": capture_elapsed_ms,
                },
            )

    def _on_frame(self, image) -> None:
        """相机 SDK 回调：仅登记、入队和记录队列水位。"""

        if not self._accepting_frames or self._stop.is_set():
            return

        now = time.perf_counter()
        with self._lock:
            sequence = self._received
            self._received += 1
            if self._first_frame_ts == 0.0:
                self._first_frame_ts = now
                self._first_frame_event.set()

        try:
            self._queue.put_nowait((image, sequence))
        except queue.Full:
            # 不静默丢帧。不能从 SDK 回调里直接 stop_grabbing，避免在
            # SDK 的回调重入路径中调用停流；后续 Pipeline 会看到 error
            # 并终止本轮扫描，然后由 stop_and_drain 安全停流。
            with self._lock:
                self._dropped += 1
                if self._error is None:
                    self._error = (
                        "连续采集评价队列已满，处理速度低于相机采集速度"
                    )
            self._accepting_frames = False
            self._stop.set()
            return

        with self._lock:
            self._enqueued += 1
            self._queue_peak = max(
                self._queue_peak,
                self._queue.qsize(),
            )

    def _worker(self) -> None:
        try:
            while not self._stop.is_set() or not self._queue.empty():
                if self._cancel is not None and self._cancel.is_set():
                    with self._lock:
                        if self._error is None:
                            self._error = "用户取消连续采集"
                    self._accepting_frames = False
                    self._stop.set()
                    break

                try:
                    image, sequence = self._queue.get(timeout=0.02)
                except queue.Empty:
                    if (
                        not self._first_frame_event.is_set()
                        and self._start_ts > 0
                        and time.perf_counter() - self._start_ts
                        > self._first_frame_timeout_s
                    ):
                        with self._lock:
                            if self._error is None:
                                self._error = (
                                    "连续采集启动后在 "
                                    f"{self._first_frame_timeout_s:g}s 内没有收到图像"
                                )
                        self._accepting_frames = False
                        self._stop.set()
                    continue

                try:
                    self._process_frame(image, sequence)
                except Exception as error:
                    with self._lock:
                        self._error = (
                            f"连续采集第{sequence}帧处理失败: {error}"
                        )
                    self._accepting_frames = False
                    self._stop.set()
                finally:
                    self._queue.task_done()
        finally:
            # 正常结束时，取流由 stop_and_drain 的调用线程停止。这里不直接
            # 调 SDK，避免和主流程的运动结束/相机清理发生并发竞争。
            pass

    def _process_frame(self, image, sequence: int) -> None:
        if self._resolved_roi is None:
            self._resolved_roi = self._normalize_roi(image)

        score_t0 = time.perf_counter()
        score = float(
            self._evaluator.evaluate_image(image, self._resolved_roi)
        )
        score_ms = (time.perf_counter() - score_t0) * 1000

        preview_image = None
        with self._lock:
            self._score_ms += score_ms
            if score > self._best_score:
                self._best_score = score
                self._best_index = sequence
                self._best_image = (
                    image.copy() if hasattr(image, "copy") else image
                )
                preview_image = self._best_image
            self._processed += 1
            self._last_processed_ts = time.perf_counter()

        # 只为新的最佳帧刷新 GUI；调用发生在后台评价线程，不是 SDK 回调。
        if preview_image is not None and self._preview_callback is not None:
            self._preview_callback(preview_image, sequence, score)

    def _normalize_roi(self, image) -> LocalRoi:
        height, width = image.shape[:2]
        if self._evaluation_roi is None:
            return 0, 0, int(width), int(height)
        values = tuple(int(value) for value in self._evaluation_roi)
        if len(values) != 4:
            raise ValueError("清晰度 ROI 必须是 (x, y, width, height)")
        x, y, roi_width, roi_height = values
        if (
            x < 0 or y < 0 or roi_width <= 0 or roi_height <= 0
            or x + roi_width > width
            or y + roi_height > height
        ):
            raise ValueError(
                f"清晰度 ROI 越界: {values}, 图像={width}x{height}"
            )
        return values
