import glob
import logging
import os
import time
from typing import (
    Dict,
    List,
    Optional,
    Tuple,
)

import cv2
import numpy as np

from adapters.evaluator_opencv import (
    OpenCVSharpnessEvaluator,
)
from backend.camera_utils import (
    fallback_roi,
    frame_positions,
    set_coarse_frame,
    set_full_frame,
)
from backend.collector import (
    PhaseCollector,
    save_jpg,
    save_phase_images,
)
from backend.config import FocusConfig
from backend.detection import detect_roi
from backend.result import (
    CalibrateResult,
    SearchResult,
)
from backend.strategies import (
    STRATEGIES,
    SearchContext,
)
from focus_template import FocusTemplate


logger = logging.getLogger(__name__)


def build_sim_scores(n: int, start_um: int, step_um: int, peak_um: float,
                     sigma_um: float = 300.0) -> list[float]:
    """合成单峰清晰度曲线（sim 测试用）：位置域抛物线形，峰在 peak_um。"""
    positions = frame_positions(n, start_um, step_um)
    return [max(0.0, 1000.0 * (1.0 - ((p - peak_um) / sigma_um) ** 2))
            for p in positions]


def compute_interval(
    coarse_positions: List[int],
    scores: List[float],
    coarse_step: int,
    search_start: int,
    search_span: int,
) -> Tuple[int, int, int, int]:
    """峰值 ±1 个粗扫点；峰在端点时向内 2 个粗扫点。返回 (lo, hi, best_idx, peak_um)。"""
    n = len(scores)
    best = max(range(n), key=lambda i: scores[i])
    peak = coarse_positions[best]
    if best == 0:
        lo, hi = peak, peak + 2 * coarse_step
    elif best == n - 1:
        lo, hi = peak - 2 * coarse_step, peak
    else:
        lo, hi = peak - coarse_step, peak + coarse_step
    lo = max(lo, search_start)
    hi = min(hi, search_start + search_span)
    return lo, hi, best, peak

def run_phase(
    plc,
    cam,
    evaluator,
    start_um: int,
    end_um: int,
    step_um: int,
    expected_n: int,
    save_dir,
    start_index: int,
    flyscan_timeout: float,
    wait_timeout: float,
    save_all: bool = False,
    cancel_event=None,
    keep_images: bool = True,
    preview_callback=None,
    preview_interval_s: float = 0.1,
    phase_name: str = "",
) -> Tuple[PhaseCollector, int, float]:
    """执行一次飞拍并校验张数；返回 (collector, plc 张数, 飞拍耗时)。"""
    collector = PhaseCollector(
        cam,
        evaluator,
        save_dir=save_dir,
        start_index=start_index,
        save_all=save_all,
        cancel_event=cancel_event,
        keep_images=keep_images,
        preview_callback=preview_callback,
        preview_interval_s=preview_interval_s,
        phase_name=phase_name,
    )
    try:
        # 启动相机硬件触发、注册回调，并启动图像评价线程。
        collector.start()

        # 触发 PLC 飞拍，并记录 PLC 飞拍阶段耗时。
        t0 = time.perf_counter()
        count = plc.flyscan_trigger(
            start_um,
            end_um,
            step_um,
            timeout_s=flyscan_timeout,
        )
        dur = time.perf_counter() - t0

        # PLC 飞拍返回以后，立即检查用户是否已经请求取消。
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("用户取消")

        # 等待 Collector 完成指定数量图像的清晰度评价。
        if not collector.wait(count, wait_timeout):
            # 获取一次统计快照，保证下面日志中的各项数字
            # 来自同一个时间点。
            stats = collector.stats()

            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("用户取消")

            if collector.error is not None:
                raise RuntimeError(collector.error)

            if stats["dropped"] > 0:
                raise RuntimeError(
                    "图像处理队列溢出: "
                    f"PLC={count}, "
                    f"收到={stats['received']}, "
                    f"入队={stats['enqueued']}, "
                    f"丢弃={stats['dropped']}, "
                    f"处理={stats['processed']}"
                )

            raise RuntimeError(
                "帧处理超时: "
                f"PLC={count}, "
                f"收到={stats['received']}, "
                f"入队={stats['enqueued']}, "
                f"丢弃={stats['dropped']}, "
                f"处理={stats['processed']}"
            )

        # 正常路径下快速确认三次。
        #
        # 如果 processed 一直等于 PLC 返回数量，并且队列为空，
        # 说明所有帧已经完成评价，不必进入后面的完整稳定期。
        fast_ok = True
        for _ in range(3):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("用户取消")
            time.sleep(0.02)

            if (
                    collector.processed != count
                    or not collector.queue_empty()
            ):
                fast_ok = False
                break

        if not fast_ok:
            # 如果仍有帧进入或队列仍在变化，
            # 最多等待两秒，直到处理数量稳定至少 0.2 秒。
            deadline = time.monotonic() + 2.0
            last_processed = -1
            stable_since = time.monotonic()

            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("用户取消")

                now = collector.processed

                if now != last_processed:
                    last_processed = now
                    stable_since = time.monotonic()

                elif time.monotonic() - stable_since >= 0.2:
                    break

                time.sleep(0.02)

        # 稳定期结束以后必须执行严格张数检查。
        #
        # 如果处理数量比 PLC 返回数量少，说明发生了丢帧；
        # 如果处理数量更多，说明可能混入了额外触发。
        if collector.processed != count:
            stats = collector.stats()

            raise RuntimeError(
                "帧数不符: "
                f"PLC={count}, "
                f"收到={stats['received']}, "
                f"入队={stats['enqueued']}, "
                f"丢弃={stats['dropped']}, "
                f"处理={stats['processed']}"
            )

        # PLC 返回帧数与根据扫描参数计算出的理论帧数不一致时，
        # 当前先给出警告，但仍然允许调用方继续处理实际采集结果。
        if count != expected_n:
            logger.warning(
                "PLC 返回 %d 帧，预期 %d",
                count,
                expected_n,
            )

        # 只有完整成功时，才把仍然可供读取分数和图像的
        # Collector 交给调用方。
        #
        # 正常路径下由调用方在读取完数据后执行 collector.stop()。
        return collector, count, dur

    except Exception:
        # 只要本阶段没有成功返回，Collector 就不会被调用方拿到。
        #
        # 因此 run_phase 必须负责停止相机取流和后台评价线程，
        # 防止异常后留下旧回调或后台线程。
        collector.stop()

        # 不在这里把异常转换成另一个异常，
        # 原始异常继续交给 run_search / run_calibrate 统一处理。
        raise

def run_search(cfg) -> int:
    # ── ① 加载模板 ──
    cancel = cfg.cancel_event  # CLI 运行时没有该属性 → None
    strategy_cls = STRATEGIES.get(cfg.strategy)
    if strategy_cls is None:
        error_message = (
            f"未知搜索策略: {cfg.strategy}"
        )

        logger.error(
            "%s",
            error_message,
        )

        return SearchResult(
            rc=1,
            action="search",
            error=error_message,
        )

    strategy = strategy_cls()
    if not os.path.exists(cfg.template):
        error_message = (
            f"模板不存在: {cfg.template}，"
            "请先执行标定生成模板"
        )

        logger.error(
            "%s",
            error_message,
        )

        return SearchResult(
            rc=1,
            action="search",
            error=error_message,
        )
    template = FocusTemplate.load(cfg.template)

    ct = {}                     # 各阶段耗时统计（ms）
    t_total = time.perf_counter()

    search_start = cfg.search_start_um
    search_span = cfg.search_span_um
    search_end = search_start + search_span
    coarse_step = cfg.coarse_step_um
    n_coarse = search_span // coarse_step
    fine_step = cfg.fine_step_um
    half = cfg.fine_half_steps * fine_step

    # ── 组件选择：sim 用假硬件，real 用真硬件 ──
    sim = cfg.mode == "sim"
    sensor_size = None
    if sim:
        from autofocus_sim import FakePlcClient, SimCamera, ScoreMapEvaluator
        sim_peak_um = search_start + (n_coarse // 2) * coarse_step
        plc = FakePlcClient(search_start, search_end, n_coarse)
        cam = SimCamera(n=n_coarse, interval_s=0.001)
        evaluator = ScoreMapEvaluator(
            build_sim_scores(n_coarse, search_start, coarse_step, sim_peak_um)
        )
    else:
        plc = cam = None

    try:
        if not sim:
            from plc.client import PlcClient
            from camera import HikCamera
            plc = PlcClient(cfg.plc_host, cfg.plc_port, timeout=5.0)
            cam = HikCamera(cfg.camera_index)
            evaluator = OpenCVSharpnessEvaluator()
            t0 = time.perf_counter()
            plc.connect()
            ct["plc_connect_ms"] = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            cam.open()
            cam.set_exposure(cfg.coarse_exposure_us or cfg.exposure_us)
            cam.set_gain(cfg.gain_db)
            sensor_size = set_coarse_frame(
                cam,
                cfg.coarse_downsample,
                cfg.coarse_binning,
            )
            ct["camera_setup_ms"] = (time.perf_counter() - t0) * 1000
            if not cfg.yes:
                ans = input(
                    "⚠️  即将触发 PLC 粗扫飞拍"
                    "（Z 轴会运动），确认请输入 yes: "
                )

                if ans.strip().lower() != "yes":
                    logger.info(
                        "用户取消搜索流程"
                    )

                    return SearchResult(
                        rc=1,
                        action="search",
                        error="用户取消",
                    )

        # ── ② 策略预测 ──
        ctx = SearchContext(
            cam=cam,
            plc=plc,
            evaluator=evaluator,
            cfg=cfg,
            template=template,
        )
        t0 = time.perf_counter()
        pred = strategy.predict_peak(ctx)
        ct["predict_ms"] = (time.perf_counter() - t0) * 1000
        predicted_peak_um = pred.peak_um
        ncc_max = pred.ncc_max
        quality = pred.quality
        coarse_best_img = pred.roi_frame
        coarse_points = pred.coarse_points
        coarse_positions = [
            position for position, score in coarse_points
        ]
        coarse_scores = [
            score for position, score in coarse_points
        ]
        logger.info(
            "策略=%s: "
            "预测峰=%sµm  "
            "quality=%s  "
            "ncc_max=%.3f",
            cfg.strategy,
            predicted_peak_um,
            quality,
            ncc_max,
        )

        # ── ③ 检测定 ROI（sim 直接降级居中 ROI，不跑 YOLO）──
        if sim:
            roi = fallback_roi(
                cfg.roi_fallback_size,
                sensor_size=sensor_size,
            )
            roi_src = "fallback(sim)"
            detect_box = None
        else:
            if coarse_best_img is None:
                raise RuntimeError(
                    f"策略 {cfg.strategy} 未返回用于 ROI 检测的图像"
                )
            t0 = time.perf_counter()
            roi, roi_src, detect_box = detect_roi(
                coarse_best_img,
                cfg.detect_model,
                cfg.detect_conf,
                cfg.coarse_binning,
                cfg.roi_fallback_size,
                model=cfg.detect_model_obj,
                sensor_size=sensor_size,
            )
            ct["yolo_ms"] = (time.perf_counter() - t0) * 1000
        logger.info(
            "ROI: %s（来源: %s）",
            roi,
            roi_src,
        )
        if (
                cancel is not None
                and cancel.is_set()
        ):
            logger.info(
                "用户在 ROI 检测后取消搜索流程"
            )

            return SearchResult(
                rc=1,
                action="search",
                error="用户取消",
            )
        # ── ④ 精扫区间 ──
        if (
                quality in (
                "mismatch",
                "boundary",
        )
                and coarse_points
        ):
            logger.warning(
                "quality=%s，"
                "精扫区间降级为"
                "粗扫峰±2×粗扫步距",
                quality,
            )
            lo, hi, _, _ = compute_interval(
                coarse_positions,
                coarse_scores,
                coarse_step,
                search_start,
                search_span,
            )
        else:
            lo = max(predicted_peak_um - half, search_start)
            hi = min(predicted_peak_um + half, search_end)
        n_fine = (hi - lo) // fine_step

        # ── 切换：停流 → 开窗（sim 换成精扫专用假组件，帧数不同）──

        if sim:
            plc = FakePlcClient(lo, lo + n_fine * fine_step, n_fine)
            cam = SimCamera(n=n_fine, interval_s=0.001)
            evaluator = ScoreMapEvaluator(
                build_sim_scores(n_fine, lo, fine_step, sim_peak_um)
            )
        else:
            t0 = time.perf_counter()
            set_full_frame(cam, cfg.fine_binning)
            cam.set_roi(*roi)
            cam.set_exposure(cfg.exposure_us)
            ct["fine_switch_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        col_f, count_f, dur_f = run_phase(
            plc, cam, evaluator,
            lo, lo + n_fine * fine_step, fine_step, n_fine,
            save_dir=cfg.save_dir if cfg.save_all else None,
            start_index=100,
            flyscan_timeout=cfg.flyscan_timeout,
            wait_timeout=cfg.frame_wait_timeout,
            save_all=cfg.save_all,
            cancel_event=cancel,
            # 精扫过程预览。
            preview_callback=cfg.preview_callback,
            preview_interval_s=cfg.preview_interval_s,
            phase_name="fine",
        )
        ct["fine_ms"] = (time.perf_counter() - t0) * 1000
        fine_map = col_f.scores()
        fine_scores = [fine_map.get(i) for i in range(count_f)]
        if any(s is None for s in fine_scores):
            raise RuntimeError("精扫缺帧")
        best_f = max(range(count_f), key=lambda i: fine_scores[i])
        if (
                best_f == 0
                or best_f == count_f - 1
        ):
            logger.warning(
                "精扫最佳帧在边界（%d/%d）",
                best_f,
                count_f - 1,
            )
        fine_positions = frame_positions(count_f, lo, fine_step)
        if cfg.save_images:
            save_phase_images(col_f, count_f, fine_positions, "fine", cfg.save_images)

        # ── ⑤ 定拍（sim 只记 index，不采 final 图）──
        final_img = None
        t0 = time.perf_counter()
        if not sim:
            col_f.stop()
            set_full_frame(cam, 1)
            final_col = PhaseCollector(cam, evaluator, save_dir=cfg.save_dir, start_index=200)
            final_col.start()
        plc.move_to_position(best_f + 1)
        plc.process_complete()
        if not sim:
            deadline = time.monotonic() + cfg.final_frame_timeout
            while time.monotonic() < deadline:
                if final_col.processed >= 1:
                    break
                time.sleep(0.02)
            if final_col.processed >= 1:
                final_img = final_col.image(0)
                if cfg.save_dir and final_img is not None:
                    save_jpg(final_img, os.path.join(cfg.save_dir, "final.jpg"))
                if cfg.save_images and final_img is not None:
                    final_pos = lo + (best_f + 1) * fine_step
                    save_jpg(final_img,
                             os.path.join(cfg.save_images, f"final_{final_pos:.0f}um.jpg"))
            final_col.stop()  # ★ 补上：定拍取流结束立刻停
        else:
            logger.info(
                "[sim] 定拍 PLC index=%d"
                "（FakePlc 已记录）",
                best_f + 1,
            )

        ct["final_ms"] = (time.perf_counter() - t0) * 1000
        ct["total_ms"] = (time.perf_counter() - t_total) * 1000

        logger.info(
            "精扫: %d 帧，"
            "最佳=%d，"
            "定拍 PLC index=%d",
            count_f,
            best_f,
            best_f + 1,
        )
        logger.info(
            "策略=%s: "
            "预测峰=%sµm  "
            "quality=%s",
            cfg.strategy,
            predicted_peak_um,
            quality,
        )
        return SearchResult(  # 成功
            rc=0, action="search",
            predicted_peak_um=predicted_peak_um,
            ncc_max=ncc_max,
            quality=quality,
            fine_best=best_f,
            move_index=best_f + 1,
            fine_best_image=col_f.image(best_f) if col_f is not None else None,
            final_image=final_img,
            coarse_points=pred.coarse_points,
            fine_points=list(zip(fine_positions, fine_scores)),
            roi=roi, roi_src=roi_src, detect_box=detect_box,
            ct_ms=ct,
        )
    except Exception as e:
        exception_type = type(e).__name__
        exception_message = str(e).strip()

        if exception_message:
            error_message = exception_message
        else:
            error_message = exception_type

        # 用户取消是正常控制流程，
        # 不记录成 ERROR，也不输出异常调用栈。
        if "取消" in error_message:
            logger.info(
                "用户取消搜索流程"
            )

        else:
            # 其他异常属于搜索流程失败。
            #
            # logger.exception() 必须在 except 中调用，
            # 它会自动附加当前异常的完整 traceback。
            logger.exception(
                "搜索流程异常: "
                "mode=%s, strategy=%s",
                cfg.mode,
                cfg.strategy,
            )

        return SearchResult(
            rc=1,
            action="search",
            error=error_message,
        )
    finally:
        if cam is not None:
            try:
                cam.close()

            except Exception as cleanup_error:
                logger.warning(
                    "搜索结束时关闭相机失败: %s",
                    cleanup_error,
                )

        if plc is not None:
            try:
                plc.disconnect()

            except Exception as cleanup_error:
                logger.warning(
                    "搜索结束时断开 PLC 失败: %s",
                    cleanup_error,
                )

def run_calibrate(cfg) -> int:
    cancel = cfg.cancel_event
    # 1. 起点/跨度默认跟随 search
    cal_start = cfg.calibrate_start_um or cfg.search_start_um
    cal_span  = cfg.calibrate_span_um  or cfg.search_span_um
    cal_step  = cfg.calibrate_step_um
    n_cal = cal_span // cal_step
    if cfg.mode == "sim":
        t0 = time.perf_counter()
        n_cal = cal_span // cal_step
        # 模板峰放在 search 粗扫的峰位置（默认 span/2 处）
        n_coarse_ref = cfg.search_span_um // cfg.coarse_step_um
        sim_peak_um = cfg.search_start_um + (n_coarse_ref // 2) * cfg.coarse_step_um
        scores = build_sim_scores(n_cal, cal_start, cal_step, sim_peak_um)
        template = FocusTemplate.from_fullscan(scores, roi=None, total_images=n_cal)
        template.meta["start_um"] = cal_start
        template.meta["step_um"] = cal_step
        template.save(cfg.template)
        logger.info(
            "[sim] 模板已保存: %s"
            "（峰 index=%d，FWHM=%.2f）",
            cfg.template,
            template.peak_position,
            template.peak_width,
        )
        return CalibrateResult(
            rc = 0,
            action= "calibrate",
            peak_position= template.peak_position,
            peak_width= template.peak_width,
            peak_um= sim_peak_um,
            ct_ms= {"sim_total_ms": (time.perf_counter() - t0) * 1000}
        )

    if cfg.calibrate_images:            # 离线分支（本课）
        t_total = time.perf_counter()
        # 加载图片（按文件名排序）
        images_paths = sorted(glob.glob(cfg.calibrate_images.rstrip("/\\") + "/*.bmp"))
        if not images_paths:
            images_paths = sorted(glob.glob(cfg.calibrate_images.rstrip("/\\") + "/*.png"))
        if not images_paths:
            images_paths = sorted(glob.glob(cfg.calibrate_images.rstrip("/\\") + "/*.jpg"))
        n = len(images_paths)
        expected = n_cal
        if n != expected:
            logger.warning(
                "标定图片 %d 张 ≠ 标定网格 %d，"
                "cal_span/cal_step 可能与采图参数不一致",
                n,
                expected,
            )
        logger.info(
            "加载 %d 张标定图片",
            n,
        )
        roi = None;scores = []
        evaluator = OpenCVSharpnessEvaluator()
        t0 = time.perf_counter()
        for p in images_paths:
            arr = np.fromfile(p, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            s = evaluator.evaluate_image(img, roi)
            scores.append(s)
        elapsed = time.perf_counter() - t0

        # 生成模板
        t_tpl = time.perf_counter()
        template = FocusTemplate.from_fullscan(scores, roi=roi, total_images=n)
        template.meta["start_um"] = cal_start
        template.meta["step_um"] = cal_step
        peak_um = (
                cal_start
                + (template.peak_position + 1)
                * cal_step
        )
        # 保存
        template.save(cfg.template)
        tpl_ms = (time.perf_counter() - t_tpl) * 1000

        # 摘要
        logger.info(
            "全扫评价耗时: %.2fs",
            elapsed,
        )

        logger.info(
            "模板峰 index: %d",
            template.peak_position,
        )

        logger.info(
            "模板 FWHM: %.2f",
            template.peak_width,
        )

        logger.info(
            "标定分数范围: [%.1f, %.1f]",
            template.meta["score_min"],
            template.meta["score_max"],
        )

        logger.info(
            "模板已保存: %s",
            cfg.template,
        )
        return CalibrateResult(
                rc= 0,
                action="calibrate",
                peak_position=template.peak_position,
                peak_width=template.peak_width,
                peak_um=peak_um,
                ct_ms={"eval_ms": elapsed * 1000, "template_ms": tpl_ms,
                          "total_ms": (time.perf_counter() - t_total) * 1000})
    else:  # 真机分支
        from plc.client import PlcClient
        from camera import HikCamera
        ct = {}
        t_total = time.perf_counter()
        plc = None
        cam = None
        try:
            plc = PlcClient(cfg.plc_host, cfg.plc_port, timeout=5.0)
            cam = HikCamera(cfg.camera_index)
            evaluator = OpenCVSharpnessEvaluator()

            t0 = time.perf_counter()
            plc.connect()
            ct["plc_connect_ms"] = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            cam.open()
            cam.set_exposure(cfg.coarse_exposure_us or cfg.exposure_us)  # decimation 共用曝光
            cam.set_gain(cfg.gain_db)
            set_coarse_frame(cam,
                             cfg.calibrate_downsample or cfg.coarse_downsample,
                             cfg.calibrate_factor or cfg.coarse_binning)
            ct["camera_setup_ms"] = (time.perf_counter() - t0) * 1000

            if not cfg.yes:
                ans = input(
                    "⚠️  即将触发 PLC 全扫"
                    "（Z 轴会运动），确认请输入 yes: "
                )

                if ans.strip().lower() != "yes":
                    logger.info(
                        "用户取消标定流程"
                    )

                    return CalibrateResult(
                        rc=1,
                        action="calibrate",
                        error="用户取消标定",
                    )

            # 全扫：起点=cal_start，终点=cal_start+n_cal*step（含尾不含首）
            t0 = time.perf_counter()
            col, count, dur = run_phase(
                plc, cam, evaluator,
                cal_start, cal_start + n_cal * cal_step, cal_step, n_cal,
                save_dir=None, start_index=0,
                flyscan_timeout=cfg.flyscan_timeout,
                wait_timeout=cfg.frame_wait_timeout,
                save_all=False,
                cancel_event=cancel,
                keep_images=bool(cfg.save_images),
                # 标定过程预览。
                preview_callback=cfg.preview_callback,
                preview_interval_s=cfg.preview_interval_s,
                phase_name="calibrate",
            )
            ct["cal_flyscan_ms"] = (time.perf_counter() - t0) * 1000
            col.stop()
            if cancel is not None and cancel.is_set():
                raise RuntimeError("用户取消")
            # 按序取分数（scores 是 dict，缺帧会 KeyError，用 .get）
            scores_map = col.scores()  # 取一次
            scores = [scores_map.get(i) for i in range(count)]
            if any(s is None for s in scores):
                raise RuntimeError("标定全扫缺帧，请检查")

            t0 = time.perf_counter()
            template = FocusTemplate.from_fullscan(scores, roi=None, total_images=count)
            template.meta["start_um"] = cal_start
            template.meta["step_um"] = cal_step
            template.save(cfg.template)
            ct["template_ms"] = (time.perf_counter() - t0) * 1000
            ct["total_ms"] = (time.perf_counter() - t_total) * 1000
            peak_um = (
                    cal_start
                    + (template.peak_position + 1)
                    * cal_step
            )
            logger.info(
                "标定完成: "
                "峰 index=%d，"
                "FWHM=%.2f，"
                "位置≈%sµm",
                template.peak_position,
                template.peak_width,
                peak_um,
            )
            if cfg.save_images:
                cal_positions = frame_positions(count, cal_start, cal_step)
                save_phase_images(col, count, cal_positions, "cal", cfg.save_images)
            return CalibrateResult(
                rc=0,
                action="calibrate",
                peak_position=template.peak_position,
                peak_width=template.peak_width,
                peak_um=peak_um,
                ct_ms=ct,
            )
        except Exception as e:
            exception_type = type(e).__name__
            exception_message = str(e).strip()

            if exception_message:
                error_message = exception_message
            else:
                error_message = exception_type

            if "取消" in error_message:
                logger.info(
                    "用户取消标定流程"
                )

            else:
                logger.exception(
                    "标定流程异常: mode=%s",
                    cfg.mode,
                )

            return CalibrateResult(
                rc=1,
                action="calibrate",
                error=error_message,
            )
        finally:
            if cam is not None:
                try:
                    cam.close()

                except Exception as cleanup_error:
                    logger.warning(
                        "标定结束时关闭相机失败: %s",
                        cleanup_error,
                    )

            if plc is not None:
                try:
                    plc.disconnect()

                except Exception as cleanup_error:
                    logger.warning(
                        "标定结束时断开 PLC 失败: %s",
                        cleanup_error,
                    )
