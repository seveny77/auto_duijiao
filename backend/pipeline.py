"""当前对焦主流程：单次连续采集精扫。"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

import cv2

from adapters.evaluator_opencv import OpenCVSharpnessEvaluator
from backend.camera_utils import set_coarse_frame
from backend.collector import save_jpg
from backend.direct_fine import ContinuousBestFrameCollector
from backend.result import BestFrameReady, SearchResult


logger = logging.getLogger(__name__)


def _timestamped_final_image_path(output_dir: Optional[str]) -> Optional[str]:
    if not output_dir:
        return None
    filename = datetime.now().strftime("%Y%m%d_%H%M%S_%f.jpg")
    return os.path.abspath(os.path.join(output_dir, filename))


def save_timestamped_final_image(image, output_dir: Optional[str], *, output_path=None):
    """保存未叠加绘制的最佳原图；失败不影响对焦结果。"""

    if image is None or not output_dir:
        return None
    try:
        path = os.path.abspath(output_path or _timestamped_final_image_path(output_dir))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_jpg(image, path)
    except (OSError, cv2.error):
        logger.exception("最终原图保存失败: directory=%s", output_dir)
        return None
    logger.info("最终原图已保存: %s", path)
    return path


def _build_sim_scores(count: int) -> list[float]:
    """仿真模式用的稳定单峰分数序列。"""

    if count <= 1:
        return [1000.0]
    center = (count - 1) * 0.55
    spread = max(count * 0.3, 1.0)
    return [max(0.0, 1000.0 * (1.0 - ((index - center) / spread) ** 2))
            for index in range(count)]


def run_search(cfg) -> SearchResult:
    """连续采集和清晰度评价并行的一次完整对焦。"""

    cancel = cfg.cancel_event
    search_start = int(cfg.search_start_um)
    search_end = search_start + int(cfg.search_span_um)
    if search_end <= search_start:
        return SearchResult(rc=1, error="连续精扫终点必须大于起点")

    motion = cam = collector = None
    borrowed_camera = False
    final_image_path = None
    ct: dict = {}
    started = time.perf_counter()

    try:
        if cfg.mode == "sim":
            from autofocus_sim import FakeMotionBackend, ScoreMapEvaluator, SimCamera
            frame_count = 32
            motion = FakeMotionBackend(search_start, search_end)
            cam = SimCamera(n=frame_count, interval_s=0.001)
            evaluator = ScoreMapEvaluator(_build_sim_scores(frame_count))
        else:
            motion = cfg.motion_backend
            if motion is None or not motion.is_connected():
                raise RuntimeError("运动控制器未连接")
            motion.prepare_new_task()
            evaluator = OpenCVSharpnessEvaluator()
            if cfg.camera is not None:
                cam = cfg.camera
                borrowed_camera = True
                cam.stop_grabbing()
            else:
                from camera import HikCamera
                cam = HikCamera(cfg.camera_index)
                cam.open()
                set_coarse_frame(
                    cam, "decimation", cfg.camera_decimation,
                    cfg.work_roi_width_px, cfg.work_roi_height_px,
                )
            cam.set_exposure(cfg.exposure_us)
            cam.set_gain(cfg.gain_db)

        position_started = time.perf_counter()
        motion.move_to_position(
            search_start, timeout_s=cfg.flyscan_timeout, cancel_event=cancel,
        )
        ct["start_position_ms"] = (time.perf_counter() - position_started) * 1000

        preview_callback = None
        if cfg.preview_callback is not None:
            preview_callback = lambda image, sequence, score: cfg.preview_callback(
                image, "continuous", sequence, score,
            )

        collector = ContinuousBestFrameCollector(
            cam, evaluator, evaluation_roi=cfg.evaluation_roi,
            target_fps=cfg.continuous_capture_fps,
            max_queue=cfg.continuous_capture_queue_size,
            first_frame_timeout_s=cfg.continuous_first_frame_timeout_s,
            drain_timeout_s=cfg.continuous_drain_timeout_s,
            cancel_event=cancel, preview_callback=preview_callback,
        )
        collector_started = time.perf_counter()
        collector.start()
        ct["collector_start_ms"] = (time.perf_counter() - collector_started) * 1000
        if not collector.wait_for_first_frame():
            raise RuntimeError(collector.error or "连续采集首帧等待超时")

        scan_started = time.perf_counter()
        motion_result = motion.continuous_scan(
            search_start, search_end, timeout_s=cfg.flyscan_timeout,
            cancel_event=cancel, velocity_um_s=cfg.continuous_scan_velocity_um_s,
        )
        ct["continuous_motion_ms"] = (time.perf_counter() - scan_started) * 1000
        if cancel is not None and cancel.is_set():
            raise RuntimeError("用户取消")

        drain_started = time.perf_counter()
        if not collector.stop_and_drain():
            raise RuntimeError(collector.error or "连续采集停止后未能完成队列处理")
        ct["collector_stop_and_drain_ms"] = (time.perf_counter() - drain_started) * 1000
        best = collector.result()
        stats = {
            "capture_received_count": best.received_count,
            "capture_enqueued_count": best.enqueued_count,
            "capture_processed_count": best.processed_count,
            "capture_dropped_count": best.dropped_count,
            "capture_queue_peak": best.queue_peak,
            "capture_configured_fps": best.configured_fps,
            "capture_score_avg_ms": best.timings_ms.get("score_avg_ms", 0.0),
        }
        if best.resulting_fps is not None:
            stats["capture_resulting_fps"] = best.resulting_fps
        ct.update(stats)
        logger.info(
            "连续采集统计：配置=%.2ffps，收到=%d，入队=%d，处理=%d，拒绝=%d，队列峰值=%d/%d",
            best.configured_fps, best.received_count, best.enqueued_count,
            best.processed_count, best.dropped_count, best.queue_peak,
            cfg.continuous_capture_queue_size,
        )

        ct["focus_total_ms"] = (time.perf_counter() - started) * 1000
        final_image = best.best_image
        final_image_path = _timestamped_final_image_path(cfg.save_dir)
        if cfg.best_frame_ready_callback is not None:
            cfg.best_frame_ready_callback(BestFrameReady(
                image=final_image, best_index=best.best_index,
                best_score=best.best_score, evaluation_roi=best.evaluation_roi_local,
                focus_ct_ms=dict(ct), scan_end_position_um=float(motion_result.actual_end_um),
                return_target_um=float(search_start), final_image_path=final_image_path,
            ))
        save_timestamped_final_image(final_image, cfg.save_dir, output_path=final_image_path)

        return_started = time.perf_counter()
        motion.move_to_position(
            search_start, timeout_s=cfg.flyscan_timeout, cancel_event=cancel,
        )
        ct["return_to_start_ms"] = (time.perf_counter() - return_started) * 1000
        ct["total_with_return_ms"] = (time.perf_counter() - started) * 1000
        return SearchResult(
            rc=0, best_frame_index=best.best_index, best_score=best.best_score,
            final_position_um=float(search_start), final_image=final_image,
            evaluation_roi=best.evaluation_roi_local, ct_ms=ct,
            final_image_path=final_image_path,
        )
    except Exception as error:
        message = str(error).strip() or type(error).__name__
        if "取消" in message:
            logger.info("用户取消连续精扫")
        else:
            logger.exception("连续精扫流程异常")
        if motion is not None:
            try:
                motion.cancel_current_motion()
            except Exception:
                logger.exception("连续精扫失败后的运动安全清理失败")
        return SearchResult(rc=1, error=message, ct_ms=ct, final_image_path=final_image_path)
    finally:
        if collector is not None:
            try:
                collector.stop_and_drain(timeout=cfg.continuous_drain_timeout_s)
            except Exception:
                logger.warning("连续精扫结束时停止采集器失败", exc_info=True)
        if cam is not None and not borrowed_camera:
            try:
                cam.close()
            except Exception:
                logger.warning("连续精扫结束时关闭相机失败", exc_info=True)
