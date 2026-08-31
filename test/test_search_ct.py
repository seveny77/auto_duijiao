# -*- coding: utf-8 -*-
"""搜索对焦全链路 CT 测试：逐环节耗时采集与拆解数据输出。

完整跑一遍 pipeline.run_search（与 GUI 生产路径完全一致），
每轮前后用 perf.reset()/perf.stats() 隔离出该轮全部环节的
CT 样本（搜索级 / 阶段级 / 飞拍内部 / 硬件调用），落盘 JSON
与 Markdown 拆解表，作为《对焦CT拆解报告》的数据来源。

用法：
    # 仿真模式（无硬件，验证脚本与埋点链路）
    python app/test/test_search_ct.py --mode sim --runs 3

    # 真实硬件（回零 + 真实飞拍，需现场确认）
    python app/test/test_search_ct.py --mode real --runs 5 --home

输出（output/search_ct/<时间戳>/）：
    summary.json   每轮全部环节 CT 聚合（n/total/mean/min/max）
    ct_report.md   跨轮拆解表，按每轮总耗时降序
"""

import argparse
import dataclasses
import json
import logging
import platform
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import perf
from backend.config import FocusConfig
from backend.pipeline import run_search


logger = logging.getLogger("search_ct")

# gui/config.json 的中文枚举 → FocusConfig 值（与 ConfigService 一致）
MODE_MAP = {"真实": "real", "仿真": "sim"}
DECIMATION_MAP = {"1x1": 1, "2x2": 2, "4x4": 4}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="搜索对焦逐环节CT测试（sim/real 两种模式）",
    )
    parser.add_argument(
        "--config",
        default="gui/config.json",
        help="GUI 配置文件（含 motion 节与搜索参数）",
    )
    parser.add_argument(
        "--mode",
        choices=["sim", "real"],
        default="sim",
        help="sim=仿真硬件离线跑通；real=真实M60+E4O4+相机",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="重复搜索次数（默认3，取跨轮均值）",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--output-dir",
        default="output/search_ct",
    )
    parser.add_argument(
        "--home",
        action="store_true",
        help="（real）明确授权本脚本执行真实回零与飞拍运动",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过现场交互确认，仅重复测试时使用",
    )
    parser.add_argument(
        "--clear-alarm",
        action="store_true",
        help="（real）回零前先复位一次轴报警",
    )
    parser.add_argument(
        "--keep-servo-on",
        action="store_true",
        help="（real）测试结束后保持使能，按Enter再安全断开",
    )
    parser.add_argument(
        "--resident-camera",
        action="store_true",
        help=(
            "（real）模拟GUI常驻相机：整个测试只open一次，"
            "逐轮注入句柄（对比默认的每轮open/close）"
        ),
    )
    parser.add_argument(
        "--incremental-e4o4",
        action="store_true",
        help=(
            "（real）E4O4比较器增量下发：段间跳过恒定量重写与回读，"
            "只下发逐段变化的位置表（对比默认的全量配置路径）"
        ),
    )
    parser.add_argument(
        "--reverse-single-capture",
        action="store_true",
        help=(
            "（real）单点飞拍改反向越过（PreCmp dir=1）：从目标上方"
            "下越触发；轴已在目标上方时跳过准备定位直接起扫"
            "（对比默认的正向上越）"
        ),
    )
    return parser


def resolve_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_gui_config(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_focus_config(
    data: dict,
    mode: str,
    camera_index: int,
) -> FocusConfig:
    """gui/config.json → FocusConfig，键映射与 GUI ConfigService 保持一致。"""

    cfg = FocusConfig()
    cfg.action = "search"
    cfg.mode = mode
    cfg.strategy = data.get("strategy", "ncc")
    # 现场确认由本脚本自己完成，不让 pipeline 再问一次。
    cfg.yes = True

    cfg.template = str(resolve_path(
        data.get("template", "data/template_sim.json"),
    ))
    cfg.dl_model = str(resolve_path(
        data.get("dl_model", "assets/models/ai/best_resnet.pt"),
    ))
    shot = data.get("shot_position_um")
    cfg.shot_position_um = int(shot) if shot is not None else None

    cfg.exposure_us = int(data.get("exposure_us", 3000))
    cfg.gain_db = float(data.get("gain_db", 0.0))
    cfg.coarse_binning = DECIMATION_MAP.get(
        data.get("decimation", "2x2"),
        2,
    )
    cfg.coarse_downsample = "decimation"

    cfg.search_start_um = int(data.get("search_start_um", 9500))
    cfg.search_span_um = int(data.get("search_span_um", 2000))
    cfg.coarse_step_um = int(data.get("coarse_step_um", 100))
    cfg.fine_step_um = int(data.get("fine_step_um", 5))
    cfg.fine_half_steps = int(data.get("fine_half_steps", 5))

    save_dir = (data.get("save_dir") or "").strip()
    cfg.save_images = save_dir or None
    cfg.camera_index = camera_index
    return cfg


def load_and_warm_detect_model(model_path: Path):
    """预热 YOLO 并返回模型对象（镜像 GUI DetectionModel 预加载）。

    生产环境 YOLO 由 GUI 启动时加载注入，模型加载/首次推理开销
    不属于搜索 CT，因此这里同样移出测量区间。
    """

    if not model_path.is_file():
        logger.warning(
            "YOLO模型不存在，搜索将走降级ROI: %s",
            model_path,
        )
        return None

    import numpy as np

    try:
        from ultralytics import YOLO
    except ImportError:
        logger.warning("ultralytics 未安装，跳过模型预热")
        return None

    load_t0 = time.perf_counter()
    model = YOLO(str(model_path))
    load_ms = (time.perf_counter() - load_t0) * 1000

    # 用典型粗扫帧尺寸（4x4 decimation 后的画幅）预热一次。
    warm_t0 = time.perf_counter()
    model.predict(
        source=np.zeros((960, 1280, 3), dtype=np.uint8),
        conf=0.5,
        verbose=False,
    )
    warm_ms = (time.perf_counter() - warm_t0) * 1000
    logger.info(
        "YOLO预热完成: 加载 %.1fms + 首次推理 %.1fms（不计入搜索CT）",
        load_ms,
        warm_ms,
    )
    return model


def run_rounds(
    cfg: FocusConfig,
    runs: int,
    motion_backend=None,
    detect_model_obj=None,
    camera=None,
) -> list:
    """执行 runs 轮搜索；每轮 perf.reset() 隔离并收集该轮全部 CT。"""

    rounds = []
    for index in range(1, runs + 1):
        cfg.motion_backend = motion_backend
        cfg.detect_model_obj = detect_model_obj
        cfg.camera = camera
        cfg.cancel_event = None

        perf.reset()
        logger.info("════ 第 %d/%d 轮搜索开始 ════", index, runs)
        t0 = time.perf_counter()
        result = run_search(cfg)
        wall_ms = (time.perf_counter() - t0) * 1000
        stats = perf.stats()

        rounds.append({
            "run": index,
            "rc": result.rc,
            "error": result.error,
            "wall_ms": wall_ms,
            "total_ms": result.ct_ms.get("total_ms", 0.0),
            "total_with_cleanup_ms": result.ct_ms.get(
                "total_with_cleanup_ms", 0.0
            ),
            "quality": result.quality,
            "ncc_max": result.ncc_max,
            "final_position_um": result.final_position_um,
            "stats": stats,
        })
        logger.info(
            "第 %d/%d 轮结束: rc=%d wall=%.1fms total=%.1fms",
            index,
            runs,
            result.rc,
            wall_ms,
            rounds[-1]["total_ms"],
        )
        if result.rc != 0:
            logger.error("搜索失败，提前结束: %s", result.error)
            break
    return rounds


def aggregate(rounds: list) -> dict:
    """跨轮聚合：每个环节的每轮总耗时均值与占比。"""

    completed = [r for r in rounds if r["rc"] == 0]
    if not completed:
        return {"runs_used": 0, "mean_total_ms": 0.0, "keys": {}}

    n_runs = len(completed)
    mean_total = sum(r["total_ms"] for r in completed) / n_runs

    keys = {}
    all_names = set()
    for r in completed:
        all_names.update(r["stats"].keys())
    for name in all_names:
        per_run_total = []
        n_samples = 0
        max_ms = 0.0
        mean_of_calls = []
        for r in completed:
            s = r["stats"].get(name)
            if s is None:
                per_run_total.append(0.0)
                continue
            per_run_total.append(s["total"])
            n_samples += s["n"]
            max_ms = max(max_ms, s["max"])
            mean_of_calls.append(s["mean"])
        total_mean = sum(per_run_total) / n_runs
        keys[name] = {
            "per_run_total_ms": total_mean,
            "pct_of_total": (
                total_mean / mean_total * 100 if mean_total else 0.0
            ),
            "samples_per_run": n_samples / n_runs,
            "call_mean_ms": (
                sum(mean_of_calls) / len(mean_of_calls)
                if mean_of_calls
                else 0.0
            ),
            "max_ms": max_ms,
        }
    return {
        "runs_used": n_runs,
        "mean_total_ms": mean_total,
        "keys": keys,
    }


def write_markdown(path: Path, mode: str, agg: dict, rounds: list) -> None:
    lines = [
        "# 搜索对焦 CT 拆解表",
        "",
        f"- 模式: {mode}",
        f"- 有效轮数: {agg['runs_used']}",
        f"- 每轮搜索总耗时均值: {agg['mean_total_ms']:.1f}ms",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "占比 = 该环节每轮总耗时均值 ÷ 每轮搜索总耗时均值。",
        "次数/轮 含轮询类硬件读数（如 wait_motion_complete 内部 20ms 轮询）。",
        "",
        "| 环节 | 标签 | 每轮总ms | 占比% | 次数/轮 | 单次均值ms | 最大ms |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    ordered = sorted(
        agg["keys"].items(),
        key=lambda kv: -kv[1]["per_run_total_ms"],
    )
    for name, s in ordered:
        if s["per_run_total_ms"] < 0.05 and s["samples_per_run"] < 1:
            continue
        lines.append(
            "| `{}` | {} | {:.1f} | {:.1f} | {:.1f} | {:.2f} | {:.1f} |".format(
                name,
                perf._label_of(name),
                s["per_run_total_ms"],
                s["pct_of_total"],
                s["samples_per_run"],
                s["call_mean_ms"],
                s["max_ms"],
            )
        )
    lines += [
        "",
        "## 每轮概览",
        "",
        "| 轮 | rc | wall_ms | total_ms | 含清理ms | 质量 | 最终位置um |",
        "|---:|---:|---:|---:|---:|---|---:|",
    ]
    for r in rounds:
        lines.append(
            "| {} | {} | {:.1f} | {:.1f} | {:.1f} | {} | {:.1f} |".format(
                r["run"],
                r["rc"],
                r["wall_ms"],
                r["total_ms"],
                r["total_with_cleanup_ms"],
                r["quality"] or "-",
                r["final_position_um"],
            )
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.runs <= 0:
        print("[STOP] --runs 必须大于0")
        return 2

    config_path = resolve_path(args.config)
    data = load_gui_config(config_path)
    cfg = build_focus_config(data, args.mode, args.camera_index)

    if not Path(cfg.template).is_file():
        print(f"[STOP] 模板不存在: {cfg.template}")
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = resolve_path(args.output_dir) / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[SEARCH CT] mode={args.mode} runs={args.runs}")
    print(
        "[PARAM] search={}..{}um step={}um(粗)/{}um(精) template={}".format(
            cfg.search_start_um,
            cfg.search_start_um + cfg.search_span_um,
            cfg.coarse_step_um,
            cfg.fine_step_um,
            Path(cfg.template).name,
        )
    )

    motion_backend = None
    detect_model_obj = None

    if args.mode == "real":
        from motion.lct import LctMotionBackend, LctMotionConfig

        print("[REAL] 本脚本将执行回零、硬件触发和真实轴飞拍")
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

        motion_values = data.get("motion")
        if not isinstance(motion_values, dict):
            print(f"[STOP] 配置缺少motion对象: {config_path}")
            return 2
        motion_config = LctMotionConfig(**motion_values)
        if args.incremental_e4o4:
            motion_config = dataclasses.replace(
                motion_config,
                e4o4_incremental_config=True,
            )
            print("[PARAM] E4O4比较器增量下发已启用")
        if args.reverse_single_capture:
            motion_config = dataclasses.replace(
                motion_config,
                single_capture_direction=1,
            )
            print("[PARAM] 单点飞拍反向越过已启用（PreCmp dir=1）")

        detect_model_obj = load_and_warm_detect_model(
            Path(cfg.detect_model),
        )

        motion_backend = LctMotionBackend(motion_config)
        resident_camera = None
        try:
            motion_backend.connect()
            if args.clear_alarm:
                motion_backend.clear_alarm()
            home_state = motion_backend.home(
                timeout_s=motion_config.home_timeout_s,
            )
            logger.info("回零完成: %s", home_state)

            if args.resident_camera:
                from camera import HikCamera

                t0 = time.perf_counter()
                resident_camera = HikCamera(args.camera_index)
                resident_camera.open()
                logger.info(
                    "常驻相机已打开 %.1fms（不计入任何一轮搜索CT）",
                    (time.perf_counter() - t0) * 1000,
                )

            rounds = run_rounds(
                cfg,
                args.runs,
                motion_backend=motion_backend,
                detect_model_obj=detect_model_obj,
                camera=resident_camera,
            )
        finally:
            if resident_camera is not None:
                resident_camera.close()
                logger.info("常驻相机已关闭")
            if args.keep_servo_on:
                input(
                    "[HOLD] 轴保持使能；确认后按Enter执行安全断开: "
                )
            motion_backend.disconnect()
    else:
        rounds = run_rounds(cfg, args.runs)

    agg = aggregate(rounds)
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "config_path": str(config_path),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "parameters": {
            "search_start_um": cfg.search_start_um,
            "search_span_um": cfg.search_span_um,
            "coarse_step_um": cfg.coarse_step_um,
            "fine_step_um": cfg.fine_step_um,
            "fine_half_steps": cfg.fine_half_steps,
            "coarse_binning": cfg.coarse_binning,
            "exposure_us": cfg.exposure_us,
            "gain_db": cfg.gain_db,
            "strategy": cfg.strategy,
            "template": cfg.template,
            "resident_camera": bool(args.resident_camera),
            "incremental_e4o4": bool(args.incremental_e4o4),
            "reverse_single_capture": bool(args.reverse_single_capture),
        },
        "rounds": rounds,
        "aggregate": agg,
    }
    summary_path = output_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)

    report_path = output_dir / "ct_report.md"
    write_markdown(report_path, args.mode, agg, rounds)

    ok_rounds = [r for r in rounds if r["rc"] == 0]
    print(
        "[RESULT] 完成 {}/{} 轮，每轮搜索总耗时均值 {:.1f}ms".format(
            len(ok_rounds),
            args.runs,
            agg["mean_total_ms"],
        )
    )
    print(f"[OUTPUT] {summary_path}")
    print(f"[OUTPUT] {report_path}")
    return 0 if len(ok_rounds) == args.runs else 1


if __name__ == "__main__":
    sys.exit(main())
