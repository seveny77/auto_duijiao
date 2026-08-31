# -*- coding: utf-8 -*-
"""M60+E4O4+相机单程精扫真实CT验证。"""

import argparse
from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
import time
from typing import Iterable

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.evaluator_opencv import OpenCVSharpnessEvaluator
from backend.camera_utils import align_window, set_full_frame
from backend.direct_fine import DirectFineCollector
from backend.detection import detect_local_roi
from motion.lct import LctMotionBackend, LctMotionConfig


logger = logging.getLogger("direct_fine_ct")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="单次精扫、首帧YOLO、整批清晰度评价CT验证",
    )
    parser.add_argument(
        "--config",
        default="gui/config.json",
        help="包含motion配置的GUI配置文件",
    )
    parser.add_argument("--model", default="assets/models/yolo/best.pt")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--exposure", type=float, default=800.0)
    parser.add_argument("--gain", type=float, default=0.0)
    parser.add_argument(
        "--roi",
        type=int,
        nargs=4,
        metavar=("X", "Y", "WIDTH", "HEIGHT"),
        default=(2660, 1568, 1000, 1000),
    )
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--start", type=int, default=19600)
    parser.add_argument("--span", type=int, default=200)
    parser.add_argument("--step", type=int, default=5)
    parser.add_argument(
        "--speeds",
        default="100,200,500",
        help="依次测试的飞扫速度，单位um/s",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--motion-timeout", type=float, default=30.0)
    parser.add_argument("--frame-timeout", type=float, default=10.0)
    parser.add_argument(
        "--output-dir",
        default="output/direct_fine_ct",
    )
    parser.add_argument(
        "--save-all",
        action="store_true",
        help="保存全部帧；会引入磁盘IO，不用于正式CT比较",
    )
    parser.add_argument(
        "--clear-alarm",
        action="store_true",
        help="回零前先调用一次轴报警复位",
    )
    parser.add_argument(
        "--home",
        action="store_true",
        help="明确授权本次脚本执行真实回零与运动",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过现场参数确认，仅建议重复测试时使用",
    )
    parser.add_argument(
        "--keep-servo-on",
        action="store_true",
        help="测试成功后保持使能，按Enter后再安全断开",
    )
    return parser


def parse_speeds(raw: str) -> list[float]:
    values = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        value = float(text)
        if value <= 0:
            raise ValueError(f"飞扫速度必须大于0: {value}")
        values.append(value)
    if not values:
        raise ValueError("至少需要提供一个飞扫速度")
    return values


def resolve_project_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_motion_config(path: Path) -> LctMotionConfig:
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as file:
        config_data = json.load(file)
    motion_values = config_data.get("motion")
    if not isinstance(motion_values, dict):
        raise ValueError(f"配置文件缺少motion对象: {path}")
    return LctMotionConfig(**motion_values)


def validate_scan_arguments(args, speeds: Iterable[float]) -> int:
    if args.start < 0:
        raise ValueError(f"扫描起点不能小于0: {args.start}")
    if args.span <= 0:
        raise ValueError(f"扫描范围必须大于0: {args.span}")
    if args.step <= 0:
        raise ValueError(f"扫描步距必须大于0: {args.step}")
    if args.span % args.step != 0:
        raise ValueError(
            "扫描范围必须是步距的整数倍: "
            f"span={args.span}, step={args.step}"
        )
    if args.repeats <= 0:
        raise ValueError(f"重复次数必须大于0: {args.repeats}")
    if args.exposure <= 0:
        raise ValueError(f"曝光时间必须大于0: {args.exposure}")
    roi_x, roi_y, roi_width, roi_height = args.roi
    if roi_x < 0 or roi_y < 0:
        raise ValueError(f"硬件ROI偏移不能小于0: {args.roi}")
    if roi_width <= 0 or roi_height <= 0:
        raise ValueError(f"硬件ROI宽高必须大于0: {args.roi}")
    if not 0 < args.conf <= 1:
        raise ValueError(f"YOLO置信度必须位于(0,1]: {args.conf}")
    list(speeds)
    return args.span // args.step


def load_and_warm_model(model_path: Path, roi_shape: tuple[int, int]):
    if not model_path.is_file():
        raise FileNotFoundError(f"YOLO模型不存在: {model_path}")

    import torch
    from ultralytics import YOLO

    torch.set_num_threads(4)
    load_t0 = time.perf_counter()
    model = YOLO(str(model_path))
    load_ms = (time.perf_counter() - load_t0) * 1000

    roi_width, roi_height = roi_shape
    warmup_image = np.zeros(
        (roi_height, roi_width, 3),
        dtype=np.uint8,
    )
    warmup_t0 = time.perf_counter()
    model.predict(
        source=warmup_image,
        conf=0.5,
        verbose=False,
    )
    warmup_ms = (time.perf_counter() - warmup_t0) * 1000
    logger.info(
        "YOLO模型加载 %.1fms，按预设ROI预热 %.1fms",
        load_ms,
        warmup_ms,
    )
    return model, load_ms, warmup_ms


def save_image(path: Path, image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(
        ".jpg",
        image,
        [cv2.IMWRITE_JPEG_QUALITY, 95],
    )
    if not ok:
        raise RuntimeError(f"最佳图像JPEG编码失败: {path}")
    encoded.tofile(str(path))


def run_once(
    *,
    backend,
    camera,
    evaluator,
    model,
    capture_roi_sensor,
    detect_conf: float,
    start_um: int,
    end_um: int,
    step_um: int,
    speed_um_s: float,
    expected_count: int,
    motion_timeout_s: float,
    frame_timeout_s: float,
    save_all: bool,
    save_dir: Path,
    camera_setup_ms: float,
) -> dict:
    backend.prepare_new_task()
    collector = DirectFineCollector(
        camera=camera,
        evaluator=evaluator,
        roi_detector=lambda image: detect_local_roi(
            image=image,
            conf=detect_conf,
            model=model,
        ),
        expected_count=expected_count,
        save_all=save_all,
        save_dir=str(save_dir) if save_all else None,
    )

    core_t0 = time.perf_counter()
    collector_start_t0 = time.perf_counter()
    collector.start()
    collector_start_ms = (
        time.perf_counter() - collector_start_t0
    ) * 1000

    trigger_count = 0
    motion_ms = 0.0
    frame_wait_ms = 0.0
    stop_grabbing_ms = 0.0
    selection_core_ms = 0.0
    try:
        motion_t0 = time.perf_counter()
        trigger_count = backend.linear_fly_scan(
            start_um,
            end_um,
            step_um,
            timeout_s=motion_timeout_s,
            phase_name="direct_fine",
            velocity_um_s=speed_um_s,
        )
        motion_ms = (time.perf_counter() - motion_t0) * 1000

        frame_wait_t0 = time.perf_counter()
        wait_ok = collector.wait(frame_timeout_s)
        frame_wait_ms = (
            time.perf_counter() - frame_wait_t0
        ) * 1000
        if not wait_ok:
            stats = collector.stats()
            if collector.error:
                raise RuntimeError(collector.error)
            raise RuntimeError(
                "单程精扫帧处理不完整: "
                f"trigger={trigger_count}, "
                f"received={stats['received']}, "
                f"enqueued={stats['enqueued']}, "
                f"dropped={stats['dropped']}, "
                f"processed={stats['processed']}"
            )
        # collector.wait()成功返回时，全部分数和最佳帧已经确定。
        # 因此“选图核心CT”截止到这里，不把后面的停止取流算进去。
        selection_core_ms = (
            time.perf_counter() - core_t0
        ) * 1000
    finally:
        stop_t0 = time.perf_counter()
        stopped = collector.stop(timeout=5.0)
        stop_grabbing_ms = (time.perf_counter() - stop_t0) * 1000
        if not stopped:
            raise RuntimeError("单程精扫评价线程未能按时停止")

    collection = collector.result()
    capture_phase_ms = (
        time.perf_counter() - core_t0
    ) * 1000
    counts = {
        "expected": expected_count,
        "triggered": trigger_count,
        "received": collection.received_count,
        "enqueued": collection.enqueued_count,
        "dropped": collection.dropped_count,
        "processed": collection.processed_count,
    }
    if any(
        counts[name] != expected_count
        for name in ("triggered", "received", "enqueued", "processed")
    ) or collection.dropped_count != 0:
        raise RuntimeError(f"单程精扫采集计数不一致: {counts}")

    best_position_um = (
        start_um + (collection.best_index + 1) * step_um
    )
    local_x, local_y, local_w, local_h = (
        collection.evaluation_roi_local
    )
    sensor_x, sensor_y, _, _ = capture_roi_sensor
    evaluation_roi_sensor = (
        sensor_x + local_x,
        sensor_y + local_y,
        local_w,
        local_h,
    )

    return_t0 = time.perf_counter()
    return_state = backend.move_to_position(
        start_um,
        timeout_s=motion_timeout_s,
    )
    return_ms = (time.perf_counter() - return_t0) * 1000

    result = {
        "speed_um_s": speed_um_s,
        "start_um": start_um,
        "end_um": end_um,
        "step_um": step_um,
        "counts": counts,
        "best_index": collection.best_index,
        "best_position_um": best_position_um,
        "best_score": collection.best_score,
        "scores": [
            collection.scores[index]
            for index in range(expected_count)
        ],
        "capture_roi_sensor": list(capture_roi_sensor),
        "evaluation_roi_local": list(
            collection.evaluation_roi_local
        ),
        "evaluation_roi_sensor": list(evaluation_roi_sensor),
        "roi_source": collection.roi_source,
        "quality": (
            "ok"
            if collection.roi_source == "detect"
            else "degraded"
        ),
        "detect_box_local": (
            list(collection.detect_box_local)
            if collection.detect_box_local is not None
            else None
        ),
        "axis_return_position_um": return_state.position_um,
        "servo_enabled_after_return": return_state.servo_enabled,
        "timings_ms": {
            "camera_setup_ms": camera_setup_ms,
            "collector_start_ms": collector_start_ms,
            "motion_and_trigger_ms": motion_ms,
            "frame_wait_ms": frame_wait_ms,
            "stop_grabbing_ms": stop_grabbing_ms,
            **collection.timings_ms,
            "selection_core_ms": selection_core_ms,
            "capture_phase_with_stop_ms": capture_phase_ms,
            "task_to_best_with_camera_setup_ms": (
                camera_setup_ms + selection_core_ms
            ),
            "return_to_start_ms": return_ms,
            "warm_cycle_ms": capture_phase_ms + return_ms,
            "cold_cycle_with_camera_setup_ms": (
                camera_setup_ms + capture_phase_ms + return_ms
            ),
        },
        "best_image": collection.best_image,
    }
    return result


def log_result(result: dict) -> None:
    timings = result["timings_ms"]
    counts = result["counts"]
    logger.info(
        "DIRECT FINE | speed=%.1fum/s | "
        "触发/收到/入队/处理=%d/%d/%d/%d | 丢弃=%d | "
        "best=%d @ %dum | roi=%s | quality=%s",
        result["speed_um_s"],
        counts["triggered"],
        counts["received"],
        counts["enqueued"],
        counts["processed"],
        counts["dropped"],
        result["best_index"],
        result["best_position_um"],
        result["evaluation_roi_local"],
        result["quality"],
    )
    logger.info(
        "DIRECT CT | 采集启动 %.1fms | 运动链路 %.1fms | "
        "首帧等待 %.1fms | YOLO %.1fms | 评价总计 %.1fms | "
        "帧等待 %.1fms | 停止取流 %.1fms",
        timings["collector_start_ms"],
        timings["motion_and_trigger_ms"],
        timings["first_frame_wait_ms"],
        timings["yolo_ms"],
        timings["score_total_ms"],
        timings["frame_wait_ms"],
        timings["stop_grabbing_ms"],
    )
    logger.info(
        "DIRECT CT总览 | 选图核心 %.1fms | 含相机设置到最佳图 %.1fms | "
        "返回起点 %.1fms | 暖周期 %.1fms | 含相机设置完整周期 %.1fms",
        timings["selection_core_ms"],
        timings["task_to_best_with_camera_setup_ms"],
        timings["return_to_start_ms"],
        timings["warm_cycle_ms"],
        timings["cold_cycle_with_camera_setup_ms"],
    )


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    speeds = parse_speeds(args.speeds)
    expected_count = validate_scan_arguments(args, speeds)
    config_path = resolve_project_path(args.config)
    model_path = resolve_project_path(args.model)
    output_root = resolve_project_path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    motion_config = load_motion_config(config_path)
    capture_roi_requested = tuple(args.roi)
    motion_config = replace(
        motion_config,
        line_scan_overrun_um=20.0,
    )
    end_um = args.start + args.span

    print("[REAL DIRECT FINE CT] 将执行回零、硬件触发和真实轴运动")
    print(
        f"[PARAM] scan={args.start}..{end_um}um, step={args.step}um, "
        f"frames={expected_count}, speeds={speeds}, repeats={args.repeats}"
    )
    print(
        f"[PARAM] sensor_roi={capture_roi_requested}, "
        f"exposure={args.exposure}us, overrun=20um"
    )
    if not args.home:
        print("[STOP] 未提供--home，本脚本不会连接或产生真实运动")
        return 2
    if not args.yes:
        answer = input(
            "确认机械区域安全、急停可用，并允许回零和真实飞扫。"
            "请输入 RUN 继续: "
        )
        if answer.strip().upper() != "RUN":
            print("[CANCELLED] 用户未确认，未连接硬件")
            return 2

    roi_width = capture_roi_requested[2]
    roi_height = capture_roi_requested[3]
    model, model_load_ms, model_warmup_ms = load_and_warm_model(
        model_path,
        (roi_width, roi_height),
    )

    from camera.camera_adapter import HikCamera

    backend = LctMotionBackend(motion_config)
    camera = HikCamera(args.camera_index)
    evaluator = OpenCVSharpnessEvaluator()
    all_results = []
    camera_opened = False
    test_succeeded = False
    camera_setup_ms = 0.0

    try:
        backend.connect()
        if args.clear_alarm:
            backend.clear_alarm()
        home_state = backend.home(timeout_s=motion_config.home_timeout_s)
        logger.info("回零完成，运动状态: %s", home_state)

        camera_setup_t0 = time.perf_counter()
        camera.open()
        camera_opened = True
        camera.set_exposure(args.exposure)
        camera.set_gain(args.gain)
        sensor_size = set_full_frame(camera, 1)
        capture_roi_sensor = align_window(
            capture_roi_requested,
            sensor_size=sensor_size,
        )
        camera.set_roi(*capture_roi_sensor)
        camera_setup_ms = (
            time.perf_counter() - camera_setup_t0
        ) * 1000
        logger.info(
            "相机设置完成: sensor=%s, roi=%s, %.1fms",
            sensor_size,
            capture_roi_sensor,
            camera_setup_ms,
        )

        for speed in speeds:
            speed_best_positions = []
            for repeat_index in range(1, args.repeats + 1):
                logger.info(
                    "开始速度档 %.1fum/s，第%d/%d轮",
                    speed,
                    repeat_index,
                    args.repeats,
                )
                run_dir = (
                    output_root
                    / f"speed_{speed:g}"
                    / f"run_{repeat_index:02d}"
                )
                result = run_once(
                    backend=backend,
                    camera=camera,
                    evaluator=evaluator,
                    model=model,
                    capture_roi_sensor=capture_roi_sensor,
                    detect_conf=args.conf,
                    start_um=args.start,
                    end_um=end_um,
                    step_um=args.step,
                    speed_um_s=speed,
                    expected_count=expected_count,
                    motion_timeout_s=args.motion_timeout,
                    frame_timeout_s=args.frame_timeout,
                    save_all=args.save_all,
                    save_dir=run_dir,
                    camera_setup_ms=camera_setup_ms,
                )
                image_path = run_dir / "best.jpg"
                save_image(image_path, result.pop("best_image"))
                result["best_image_path"] = str(image_path)
                all_results.append(result)
                speed_best_positions.append(
                    result["best_position_um"]
                )
                log_result(result)

            position_spread = (
                max(speed_best_positions) - min(speed_best_positions)
            )
            if position_spread > args.step:
                raise RuntimeError(
                    "同一速度最佳位置波动超过一个步距，停止升级速度: "
                    f"speed={speed}, positions={speed_best_positions}, "
                    f"allowed={args.step}um"
                )
            logger.info(
                "速度档 %.1fum/s 验收通过: positions=%s",
                speed,
                speed_best_positions,
            )

        test_succeeded = True
        summary = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "config_path": str(config_path),
            "model_path": str(model_path),
            "model_load_ms": model_load_ms,
            "model_warmup_ms": model_warmup_ms,
            "camera_setup_ms": camera_setup_ms,
            "parameters": {
                "capture_roi_sensor": list(capture_roi_sensor),
                "start_um": args.start,
                "end_um": end_um,
                "span_um": args.span,
                "step_um": args.step,
                "expected_count": expected_count,
                "speeds_um_s": speeds,
                "repeats": args.repeats,
                "exposure_us": args.exposure,
                "gain_db": args.gain,
                "line_scan_overrun_um": 20.0,
            },
            "runs": all_results,
        }
        summary_path = output_root / "summary.json"
        with summary_path.open("w", encoding="utf-8") as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)
        print(f"[OK] 单程精扫CT验证通过，汇总: {summary_path}")

        if args.keep_servo_on:
            input(
                "[HOLD] 轴已返回扫描起点并保持使能；"
                "确认后按Enter执行安全断开: "
            )
        return 0

    finally:
        if camera_opened:
            try:
                camera.close()
            except Exception:
                logger.exception("关闭相机失败")
        try:
            backend.disconnect()
        except Exception:
            logger.exception("安全断开运动后端失败")
        try:
            HikCamera.shutdown()
        except Exception:
            logger.exception("关闭相机SDK失败")
        if test_succeeded:
            print("[CLEANUP] 相机已关闭，轴已去使能，运动控制器已断开")
        else:
            print("[CLEANUP] 异常路径已执行停止、去使能和设备清理")


if __name__ == "__main__":
    raise SystemExit(main())
