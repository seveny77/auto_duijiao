# -*- coding: utf-8 -*-
"""最终成像模糊判别实验（真实运动+真实相机，静态/飞拍清晰度对比）。

背景：GUI 实测开启 B（反向直通）后最终图发糊，且该轮精扫最佳帧贴窗顶
（预测峰 8450、best=9/9@8475）——存在两个竞争假设：
  H1 焦点位置错：真焦峰在精扫窗上方被截断，8475 本身已离焦（与 B 无关）；
  H2 飞拍方式变：下行穿越/短跑道（B 的改动）导致成像变糊
     （反向间隙或加速未稳区曝光）。

一次上机同时回答：
  A 静态清晰度地图 8450..8530µm 步距5（软触发、逐点停稳采图）
    → 找真焦峰 P，并量化 8475 相对 P 的离焦程度（H1 直接判据）；
  B 同一位置 P 的四种飞拍（硬触发，均过 P 一次）：
      up50   上行·50µm 跑道（= 旧生产路径物理形态）
      down50 下行·50µm 跑道（只改方向——判 H2 反向间隙）
      down20 下行·20µm 跑道（复现 B 最不利直通几何——判短跑道）
      up20   上行·20µm 跑道（对称性对照）
    每臂静态参考紧随其后采一张（跟踪漂移），输出 拉普拉斯方差 比。
  判读：若四臂 ≈ 同位置静态 → H2 排除，糊=焦点位置错；若仅 down* 低 →
  反向间隙；若 *20 低 → 跑道不足；四臂全低 → 相机/光路状态问题。

用法（真实运动，需现场确认）：
    python test/test_focus_sharpness.py --home          # 交互确认
    python test/test_focus_sharpness.py --home --yes    # 跳过确认
"""

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.camera_utils import set_full_frame
from backend.collector import save_jpg
from camera import HikCamera
from motion.lct import LctMotionBackend, LctMotionConfig

MAP_LO_UM = 8450
MAP_HI_UM = 8530
MAP_STEP_UM = 5
APPROACH_LOW_UM = 8475          # 被怀疑离焦的现行"最佳"位置
CROP = 1024                     # 清晰度度量中心裁剪边长
FRAME_TIMEOUT_MS = 3000
MOTION_REPS = 2                 # 每个飞拍臂重复次数
MOVE_TIMEOUT_S = 10.0


def lapvar(img: np.ndarray) -> float:
    """中心裁剪 CROP² 的拉普拉斯方差（清晰度，越大越锐）。"""

    h, w = img.shape[:2]
    y0 = max(0, (h - CROP) // 2)
    x0 = max(0, (w - CROP) // 2)
    crop = img[y0:y0 + CROP, x0:x0 + CROP]
    if crop.ndim == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(crop, cv2.CV_64F).var())


def static_grab(cam: HikCamera) -> np.ndarray:
    """软触发采一张静态图（capture_frame 内部完成模式切换与取流）。"""

    return cam.capture_frame(timeout_ms=FRAME_TIMEOUT_MS)


def motion_grab(
    backend: LctMotionBackend,
    cam: HikCamera,
    position_um: int,
    direction: int,
    runway_um: int,
    timeout_s: float,
) -> np.ndarray:
    """一次飞拍：先显性走到 起始侧±跑道，再 capture_at_position 过点触发。

    先 move_to_position 到扫描起点，保证直通分支生效、跑道长度精确受控
    （不依赖上一臂的落点）。direction=0 上行 / 1 下行，与生产语义一致。
    """

    start_um = (
        position_um + runway_um
        if direction == 1
        else position_um - runway_um
    )
    backend.move_to_position(start_um, MOVE_TIMEOUT_S)
    cam.set_trigger_mode("hardware")
    cam.start_grabbing()
    try:
        count = backend.capture_at_position(
            position_um,
            timeout_s=timeout_s,
        )
        if count != 1:
            raise RuntimeError(f"触发数异常: {count}")
        img = cam.get_frame(FRAME_TIMEOUT_MS)
        if img is None:
            raise RuntimeError("硬触发后未取到帧")
        return img
    finally:
        cam.stop_grabbing()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="最终成像模糊判别实验（静态地图+四臂飞拍对比）",
    )
    parser.add_argument(
        "--config", default="gui/config.json",
        help="GUI配置文件（含motion节与曝光/增益）",
    )
    parser.add_argument(
        "--camera-index", type=int, default=0,
    )
    parser.add_argument("--home", action="store_true",
                        help="明确授权本脚本执行真实回零与飞拍运动")
    parser.add_argument("--yes", action="store_true",
                        help="跳过现场交互确认")
    parser.add_argument("--output-dir", default="output/focus_sharpness")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with config_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    motion_values = data.get("motion")
    if not isinstance(motion_values, dict):
        print(f"[STOP] 配置缺少motion对象: {config_path}")
        return 2
    base_cfg = LctMotionConfig(**motion_values)
    exposure_us = data.get("exposure_us", 800)
    gain_db = data.get("gain_db", 0.0)

    print(
        "[FOCUS-TEST] 静态地图 {}..{}µm 步距{} | 四臂飞拍 up50/down50/"
        "down20/up20 ×{}次 | 曝光{}µs".format(
            MAP_LO_UM, MAP_HI_UM, MAP_STEP_UM, MOTION_REPS, exposure_us,
        )
    )
    print("[REAL] 本脚本将执行回零、真实轴运动与真实采图")
    if not args.home:
        print("[STOP] 未提供--home，本脚本不会连接或产生真实运动")
        return 2
    if not args.yes:
        answer = input(
            "确认机械区域安全、急停可用，并允许回零和真实运动。"
            "请输入 RUN 继续: "
        )
        if answer.strip().upper() != "RUN":
            print("[CANCELLED] 用户未确认，未连接硬件")
            return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    img_dir = output_dir / stamp
    img_dir.mkdir(parents=True, exist_ok=True)

    backend = LctMotionBackend(base_cfg)
    cam = HikCamera(args.camera_index)
    results = {"static_map": {}, "motion_arms": [], "bookends": {}}
    backend.connect()
    try:
        backend.home(timeout_s=base_cfg.home_timeout_s)
        cam.open()
        cam.set_gain(gain_db)
        cam.set_exposure(exposure_us)
        set_full_frame(cam, 1)
        cam.set_exposure(exposure_us)

        # ── A. 静态清晰度地图（从下方进入，逐点上行，回避方向混入）──
        backend.move_to_position(MAP_LO_UM - 30, MOVE_TIMEOUT_S)
        best_um, best_val = None, -1.0
        for pos_um in range(MAP_LO_UM, MAP_HI_UM + 1, MAP_STEP_UM):
            backend.move_to_position(pos_um, MOVE_TIMEOUT_S)
            time.sleep(0.05)  # 停稳余量
            img = static_grab(cam)
            val = lapvar(img)
            results["static_map"][str(pos_um)] = val
            if val > best_val:
                best_um, best_val = pos_um, val
            print(
                "[FOCUS-TEST] 静态 {}µm: lapvar={:.1f}".format(pos_um, val)
            )
        save_jpg(
            static_grab(cam), str(img_dir / "static_peak_{}um.jpg".format(best_um))
        )
        peak_um = best_um
        print(
            "[FOCUS-TEST] 静态真焦峰 P={}µm (lapvar={:.1f})，"
            "参考离焦位 {}µm 地图值={:.1f}".format(
                peak_um, best_val, APPROACH_LOW_UM,
                results["static_map"].get(str(APPROACH_LOW_UM), float("nan")),
            )
        )

        # ── B. 四臂飞拍（每臂前后各采一张静态参考跟踪漂移）──
        arms = [
            ("up50", 0, 50),
            ("down50", 1, 50),
            ("down20", 1, 20),
            ("up20", 0, 20),
        ]
        for rep in range(1, MOTION_REPS + 1):
            for name, direction, runway in arms:
                arm_cfg = dataclasses.replace(
                    base_cfg,
                    single_capture_direction=direction,
                    single_capture_approach_um=runway,
                    single_capture_exit_um=50,
                )
                backend._config = arm_cfg
                # 静态参考必须在真焦峰 P 上采（飞拍臂落点在 P±50，
                # 不回 P 采到的参考本身就是离焦帧，比值失真）。
                backend.move_to_position(peak_um, MOVE_TIMEOUT_S)
                time.sleep(0.05)
                ref_before = lapvar(static_grab(cam))
                img = motion_grab(
                    backend, cam, peak_um, direction, runway,
                    timeout_s=base_cfg.home_timeout_s,
                )
                val = lapvar(img)
                backend.move_to_position(peak_um, MOVE_TIMEOUT_S)
                time.sleep(0.05)
                ref_after = lapvar(static_grab(cam))
                save_jpg(
                    img, str(img_dir / "{}_r{}_at{}um.jpg".format(
                        name, rep, peak_um))
                )
                results["motion_arms"].append({
                    "arm": name, "rep": rep,
                    "direction": direction, "runway_um": runway,
                    "position_um": peak_um,
                    "lapvar": val,
                    "static_ref_before": ref_before,
                    "static_ref_after": ref_after,
                    "ratio_vs_ref": val / max(ref_before, 1e-9),
                })
                print(
                    "[FOCUS-TEST] 飞拍 {} r{} @{}µm: lapvar={:.1f} | "
                    "静态参考 {:.1f}/{:.1f} | 比值 {:.3f}".format(
                        name, rep, peak_um, val,
                        ref_before, ref_after, val / max(ref_before, 1e-9),
                    )
                )
        backend._config = base_cfg

        # ── 收尾：8475 与 P 的静态对照（书挡）──
        for tag, pos in (("focus_peak", peak_um), ("suspect", APPROACH_LOW_UM)):
            backend.move_to_position(pos, MOVE_TIMEOUT_S)
            time.sleep(0.05)
            results["bookends"][tag] = {
                "position_um": pos, "lapvar": lapvar(static_grab(cam)),
            }
        print(
            "[FOCUS-TEST] 书挡: 真焦峰{}µm={:.1f} vs 疑点{}µm={:.1f}".format(
                peak_um,
                results["bookends"]["focus_peak"]["lapvar"],
                APPROACH_LOW_UM,
                results["bookends"]["suspect"]["lapvar"],
            )
        )
    finally:
        try:
            cam.close()
        finally:
            backend.disconnect()

    results["peak_um"] = peak_um
    results["map_range_um"] = [MAP_LO_UM, MAP_HI_UM, MAP_STEP_UM]
    out_path = output_dir / f"{stamp}.json"
    with out_path.open("w", encoding="utf-8") as file:
        json.dump(
            {"created_at": datetime.now().isoformat(timespec="seconds"),
             "exposure_us": exposure_us, "gain_db": gain_db,
             "results": results},
            file, ensure_ascii=False, indent=2,
        )
    print(f"[OUTPUT] {out_path}")
    print(f"[OUTPUT] {img_dir}")

    arm_mean = {}
    for item in results["motion_arms"]:
        arm_mean.setdefault(item["arm"], []).append(item["ratio_vs_ref"])
    print("\n[FOCUS-TEST] ═══════ 判读摘要 ═══════")
    peak_ref = results["bookends"]["focus_peak"]["lapvar"]
    suspect_ref = results["bookends"]["suspect"]["lapvar"]
    print(
        "焦点位置判据: 真焦峰 P={}µm 静态={:.1f} | 8475µm 静态={:.1f} "
        "({:.0f}%)".format(
            peak_um, peak_ref, suspect_ref,
            100.0 * suspect_ref / max(peak_ref, 1e-9),
        )
    )
    for name in ("up50", "down50", "down20", "up20"):
        vals = arm_mean.get(name, [])
        if vals:
            print(
                "飞拍 {} : 静态比 {:.3f}".format(
                    name, sum(vals) / len(vals),
                )
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
