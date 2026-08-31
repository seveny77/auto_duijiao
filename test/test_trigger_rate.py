# -*- coding: utf-8 -*-
"""触发间隔-轴速度-相机帧率 三者关系测试（真实相机+真实运动）。

原理：相机只感知触发脉冲的**时间间隔** T = 步距 ÷ 轴速度，不感知位置。
对每种相机模式（fine=1x1全幅5472x3648 / coarse=4x4降采样1368x912）测出
"最小可靠触发间隔 t_min"（帧数==脉冲数 成立的最小 T），即可推导任意组合：
    v_max(步距) = 步距 / t_min

三个部分：
  A 静态软触发（不动轴）：固定间隔连发软触发，间隔 100→3ms 逐级收紧，
    并发排水计数（SDK ImageNodeNum=1，必须边发边收，否则 SDK 层丢帧污染测量）
    → 各模式接受率-间隔曲线（相机本体软触发能力；发令节拍自带护栏校验）
  B 低速变步距硬触发（真实 E4O4 线性比较器）：固定 300µm/s，扫步距
    （加速度爬升距离仅 ~2µm，避开变速区污染触发间隔）
    → 硬触发语义下的 t_min（生产同路径，结论可迁移）。
      用变步距而非变速度控制间隔，规避高速段加速区把触发间隔拉偏的问题
  C 生产工况抽检：精扫 5µm@300/500/700、粗扫 40µm@1000/1500/2000 ×2 重复，
    验证 帧数==E4O4脉冲数 无丢帧（丢帧/多帧都是帧-位置错位，静默错误）

判读输出：每模式 t_min（软/硬分开）/ 最大可持续帧率 / 常用步距 v_max 表
（以 Part B 硬触发为准）/ 生产参数裕度 / 饱和排空尾巴（流水线深度证据）。

用法（真实运动，需现场确认）：
    python test/test_trigger_rate.py --home          # 交互确认
    python test/test_trigger_rate.py --home --yes    # 跳过确认
    python test/test_trigger_rate.py --home --yes --part a|b   # 只跑一部分
"""

import argparse
import json
import math
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.camera_utils import set_full_frame  # noqa: E402
from camera import HikCamera  # noqa: E402
from motion.lct import LctMotionBackend, LctMotionConfig  # noqa: E402

# ── 运动安全走廊：所有扫描窗口（含末端越程）必须落在 [LO, HI] 内 ──
# 该区间是生产日行路径（对焦搜索 8340..8600 + 去出片位 12000 的途经段）。
CORRIDOR_LO_UM = 8300
CORRIDOR_HI_UM = 8700
CENTER_UM = 8490            # 窗口摆放中心（焦面附近，内容有纹理便于差分）

PART_A_INTERVALS_MS = [100, 66, 50, 40, 33, 25, 20, 16, 12, 10, 8, 6, 5, 4, 3]
PART_A_TRIGGERS = 24
PART_A_DRAIN_IDLE_S = 0.4   # 触发发完后无新帧静默判定
PART_A_DRAIN_CAP_S = 15.0
SPIN_SLEEP_FLOOR_S = 0.016  # Windows sleep 粒度~15.6ms，短于此纯自旋保节拍

SWEEP_VELOCITY_UM_S = 300.0
# 探测臂 (step_um, velocity_um_s, points)：T = step/velocity。
# 点数随速度加大——高速臂加速区更长（~1.5v²/2a），保证巡航区点数占大头。
FINE_SWEEP_ARMS = [           # 1x1 全幅，全部 @300µm/s（爬升距离仅~2µm）
    (6, 300.0, 20), (5, 300.0, 20), (4, 300.0, 20),
    (3, 300.0, 24), (2, 300.0, 30), (1, 300.0, 40),
]
COARSE_SWEEP_ARMS = [         # 4x4 降采样，下探更低间隔
    (2, 300.0, 20), (1, 300.0, 30), (1, 450.0, 40), (1, 600.0, 50),
]

# 生产/路线图抽检：(mode, step, velocity, span)，各跑 SPOT_REPS 遍
SPOT_ARMS = [
    ("fine", 5, 300.0, 100),    # 现行精扫
    ("fine", 5, 500.0, 100),    # 路线图 ②500
    ("fine", 5, 700.0, 100),
    ("coarse", 40, 1000.0, 240),  # 现行粗扫（恢复1000后）
    ("coarse", 40, 1500.0, 240),  # 路线图 H
    ("coarse", 40, 2000.0, 320),
]
SPOT_REPS = 2

SCAN_TIMEOUT_S = 20.0
CAM_QUIET_IDLE_S = 0.3        # 臂间相机静默门：无新帧该时长才允许起扫
CAM_QUIET_CAP_S = 3.0
MOTION_DRAIN_IDLE_S = 0.6     # 运动后无新帧静默判停（按最后变化时刻计时）
MOTION_DRAIN_CAP_S = 6.0
AXIS_ACCEL_UM_S2 = 31250.0     # ParamCard0.ini Acc×10，估算加/减速区污染点数
RAMP_FACTOR = 1.5              # SmAcc 平滑的实测有效系数


def fingerprint(img):
    """回调热路径：中心 64x64 均值/方差指纹（~0.1ms，无 I/O、不持引用）。"""

    h, w = img.shape[:2]
    y0 = max(0, h // 2 - 32)
    x0 = max(0, w // 2 - 32)
    crop = img[y0:y0 + 64, x0:x0 + 64]
    return float(crop.mean()), float(crop.std()), int(w), int(h)


class FrameRecorder:
    """Part B 用的轻量帧回调：只计数+到达时刻+指纹（生产回调同层，内存安全）。"""

    def __init__(self):
        self.records = []          # (perf_counter, mean, std, w, h)
        self._lock = threading.Lock()

    def __call__(self, img):
        try:
            rec = (time.perf_counter(),) + fingerprint(img)
        except Exception:          # 单帧转换异常不终结回调路径
            return
        with self._lock:
            self.records.append(rec)

    def snapshot(self):
        with self._lock:
            return list(self.records)

    def count(self):
        with self._lock:
            return len(self.records)

    def reset(self):
        with self._lock:
            self.records.clear()


def wait_camera_quiet(recorder, idle_s=CAM_QUIET_IDLE_S, cap_s=CAM_QUIET_CAP_S):
    """等相机真正静默（在途帧排空）——防上一臂残留帧串进下一臂计数。"""

    base = time.perf_counter()
    last_n = recorder.count()
    last_change = base
    while time.perf_counter() - base < cap_s:
        now = time.perf_counter()
        n = recorder.count()
        if n != last_n:
            last_n = n
            last_change = now
        elif now - last_change >= idle_s:
            return True
        time.sleep(0.02)
    return False


def apply_camera_mode(cam, mode, exposure_us, gain_db):
    """停流→复位全幅→按模式降采样→曝光/增益。返回 (sensor_w, sensor_h)。"""

    cam.stop_grabbing()  # 幂等；参数下发前必须停流
    sensor = set_full_frame(cam, 1)
    if mode == "coarse":
        cam.set_decimation(4, 4)
        cam.set_roi(0, 0, sensor[0] // 4, sensor[1] // 4)
    cam.set_exposure(exposure_us)
    cam.set_gain(gain_db)
    return sensor


def window_for(span_um, step_um, overrun_um):
    """以 CENTER 为中心、向下取整到 step 倍数的窗口，校验走廊（含越程）。"""

    start = CENTER_UM - (span_um // 2)
    start = start - (start % step_um)
    end = start + span_um
    margin = math.ceil(overrun_um) + 5
    if not (CORRIDOR_LO_UM <= start and end + margin <= CORRIDOR_HI_UM):
        raise ValueError(f"窗口 {start}..{end}+越程{margin}µm 超出安全走廊 "
                         f"[{CORRIDOR_LO_UM},{CORRIDOR_HI_UM}]")
    return start, end


def summarize_deltas(times_s):
    if len(times_s) < 2:
        return {}
    deltas = [(b - a) * 1000.0 for a, b in zip(times_s, times_s[1:])]
    return {
        "n": len(deltas),
        "min_ms": round(min(deltas), 2),
        "median_ms": round(statistics.median(deltas), 2),
        "mean_ms": round(statistics.mean(deltas), 2),
        "max_ms": round(max(deltas), 2),
    }


def run_part_a(cam, mode, exposure_us, gain_db, results, out_path):
    """静态软触发：固定间隔连发，并发排水计数。"""

    print(f"\n[A] 静态软触发扫描 mode={mode}")
    sensor = apply_camera_mode(cam, mode, exposure_us, gain_db)
    cam.set_trigger_mode("software")
    cam.start_grabbing()
    try:
        for interval_ms in PART_A_INTERVALS_MS:
            interval_s = interval_ms / 1000.0
            recs = []
            recs_lock = threading.Lock()
            drain_errors = [0]
            stop_firing = threading.Event()
            drain_deadline = [None]
            last_arrival = [time.perf_counter()]

            def drain():
                while True:
                    try:
                        img = cam.get_frame(150)
                        now = time.perf_counter()
                        if img is None:
                            if stop_firing.is_set() \
                                    and drain_deadline[0] is not None \
                                    and now > drain_deadline[0]:
                                return
                            continue
                        rec = (now,) + fingerprint(img)
                        with recs_lock:
                            recs.append(rec)
                        last_arrival[0] = now
                    except Exception:
                        drain_errors[0] += 1   # 单帧异常不终结排水线程

            t = threading.Thread(target=drain, daemon=True)
            t.start()
            fire_times = []
            t_fire0 = time.perf_counter()
            next_deadline = time.perf_counter()
            try:
                for _ in range(PART_A_TRIGGERS):
                    cam.trigger_software()
                    fire_times.append(time.perf_counter())
                    next_deadline += interval_s
                    delay = next_deadline - time.perf_counter()
                    if delay > SPIN_SLEEP_FLOOR_S:
                        time.sleep(delay - 0.003)
                    while time.perf_counter() < next_deadline:
                        pass
            finally:
                # 异常路径也必须放行排水线程，否则 cam.close() 会在其仍阻塞于
                # GetImageBuffer 时销毁句柄（未定义行为）
                stop_firing.set()
                drain_deadline[0] = 0.0
            fire_elapsed = time.perf_counter() - t_fire0
            base = time.perf_counter()
            while time.perf_counter() - base < PART_A_DRAIN_CAP_S:
                now = time.perf_counter()
                if now - last_arrival[0] > PART_A_DRAIN_IDLE_S \
                        and now - t_fire0 > fire_elapsed + PART_A_DRAIN_IDLE_S:
                    break
                time.sleep(0.02)
            stop_firing.set()
            drain_deadline[0] = 0.0
            t.join(2.0)
            if t.is_alive():
                raise RuntimeError("排水线程未退出，拒绝继续（防句柄并发销毁）")

            n_frames = len(recs)
            accept = n_frames / PART_A_TRIGGERS
            nominal_ms = PART_A_TRIGGERS * interval_ms
            fire_valid = abs(fire_elapsed * 1000 - nominal_ms) \
                <= max(3.0, 0.15 * nominal_ms)
            times = [r[0] for r in recs]
            arm = {
                "part": "A", "mode": mode, "interval_ms": interval_ms,
                "triggers": PART_A_TRIGGERS, "frames": n_frames,
                "accept": round(accept, 3),
                "fire_valid": fire_valid,
                "fire_elapsed_ms": round(fire_elapsed * 1000, 1),
                "fire_deltas": summarize_deltas(fire_times),
                "drain_errors": drain_errors[0],
                "drain_tail_ms": round((times[-1] - t_fire0 - fire_elapsed) * 1000, 1)
                if times else None,
                "arrival_deltas": summarize_deltas(times),
                "sensor": list(sensor),
            }
            results.append(arm)
            write_json(out_path, results)
            print("[A] {:>3}ms: 帧 {:>2}/{}  accept {:.2f}  节拍{}  尾巴 {}ms".format(
                interval_ms, n_frames, PART_A_TRIGGERS, accept,
                "OK" if fire_valid else "失准",
                arm["drain_tail_ms"] if arm["drain_tail_ms"] is not None else "-"))
    finally:
        cam.stop_grabbing()


def run_motion_arm(backend, cam, mode, step_um, velocity, span_um, rep,
                   overrun_um, recorder, results, out_path):
    """一次硬触发扫描：臂间静默门 + 回调计数 + E4O4 脉冲对账 + 静默排空。"""

    start, end = window_for(span_um, step_um, overrun_um)
    expected = span_um // step_um
    ramp_um = RAMP_FACTOR * velocity ** 2 / (2 * AXIS_ACCEL_UM_S2)
    accel_pts = int(ramp_um // step_um)
    decel_intr = max(0.0, ramp_um - (overrun_um + step_um))
    decel_pts = int(math.ceil(decel_intr / step_um))
    cruise_pts = max(0, expected - accel_pts - decel_pts)

    wait_camera_quiet(recorder)
    recorder.reset()
    t0 = time.perf_counter()
    pulses = backend.linear_fly_scan(
        start, end, step_um, timeout_s=SCAN_TIMEOUT_S,
        phase_name="probe", velocity_um_s=velocity,
    )
    motion_return = time.perf_counter()
    # 静默排空：按最后帧变化时刻计时的真 0.6s 静默，封顶 CAP
    base = time.perf_counter()
    last_change = motion_return
    last_n = -1
    while time.perf_counter() - base < MOTION_DRAIN_CAP_S:
        now = time.perf_counter()
        n = recorder.count()
        if n != last_n:
            last_n = n
            last_change = now
        elif now - last_change > MOTION_DRAIN_IDLE_S:
            break
        if n >= pulses and now - last_change > 0.15:
            break
        time.sleep(0.05)
    snap = recorder.snapshot()
    frames = len(snap)
    times = [r[0] for r in snap]
    idx = sorted(set([0, len(snap) // 2, max(0, len(snap) - 1)]))[:3]
    arm = {
        "part": "B", "mode": mode, "step_um": step_um,
        "velocity_um_s": velocity, "span_um": span_um, "rep": rep,
        "start_um": start, "end_um": end,
        "interval_ms": round(step_um / velocity * 1000.0, 2),
        "expected_pulses": expected, "e4o4_pulses": pulses, "frames": frames,
        "accept": round(frames / max(pulses, 1), 3),
        "excess_frames": frames - pulses,
        "ramp_points_est": {"accel": accel_pts, "decel": decel_pts,
                            "cruise": cruise_pts},
        "scan_ms": round((motion_return - t0) * 1000, 1),
        "drain_tail_ms": round((times[-1] - motion_return) * 1000, 1)
        if times else None,
        "arrival_deltas": summarize_deltas(times),
        "fingerprints": {str(i): [round(snap[i][1], 1), round(snap[i][2], 1)]
                         for i in idx if 0 <= i < len(snap)},
    }
    results.append(arm)
    write_json(out_path, results)
    print("[B] {} {:>2}µm@{:>4}µm/s r{} (T={:>4}ms): 脉冲 {:>3} 帧 {:>3} "
          "accept {:.2f} 尾巴 {}ms".format(
              mode, step_um, int(velocity), rep, arm["interval_ms"],
              pulses, frames, arm["accept"],
              arm["drain_tail_ms"] if arm["drain_tail_ms"] is not None else "-"))


def write_json(out_path, results):
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corridor_um": [CORRIDOR_LO_UM, CORRIDOR_HI_UM],
        "results": results,
    }
    tmp = out_path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    tmp.replace(out_path)


def summarize(results):
    print("\n══════════ 判读摘要 ══════════")
    errors = [r for r in results if "error" in r]
    for arm in errors:
        print(f"      [异常] {arm.get('mode')} {arm.get('step_um')}µm@"
              f"{arm.get('velocity_um_s')}µm/s: {arm['error']}")
    ok = [r for r in results if "error" not in r]

    t_min = {}
    for mode in ("fine", "coarse"):
        for part in ("B", "A"):
            arms = [r for r in ok
                    if r.get("mode") == mode and r.get("part") == part
                    and r.get("fire_valid", True)
                    and r.get("accept") == 1.0]
            if not arms:
                print(f"[{mode}][Part{part}] 无全接受臂")
                continue
            best = min(arms, key=lambda r: r["interval_ms"])
            t_min[(mode, part)] = best["interval_ms"]
            print(f"[{mode}][Part{part}] t_min = {best['interval_ms']}ms"
                  f"（帧数==触发数的最小间隔）→ 可持续 ≈{1000 / best['interval_ms']:.0f}fps")
    for mode in ("fine", "coarse"):
        t = t_min.get((mode, "B")) or t_min.get((mode, "A"))
        if not t:
            continue
        fps = 1000.0 / t
        src = "B硬触发" if t_min.get((mode, "B")) else "A软触发(仅参考)"
        print(f"[{mode}] v_max（{src}）: " + "  ".join(
            f"{s}µm→{s * fps / 1000:.1f}mm/s" for s in (1, 2.5, 5, 10, 40)))
    print("生产对照: 精扫5µm@300→T=16.7ms | 粗扫40µm@1000→40ms "
          "| 路线图 5@500→10ms, 5@700→7.1ms, 40@1500→26.7ms, 40@2000→20ms")
    for arm in ok:
        label = None
        if arm.get("accept", 1.0) > 1.0:
            label = f"[多帧/串帧] {arm['mode']} {arm['interval_ms']}ms: " \
                    f"frames={arm.get('frames')} > 触发=" \
                    f"{arm.get('triggers', arm.get('e4o4_pulses'))}"
        elif arm.get("accept", 1.0) < 0.999:
            label = f"[丢帧] {arm['mode']} {arm['interval_ms']}ms: " \
                    f"accept={arm['accept']}"
        elif arm.get("part") == "A" and not arm.get("fire_valid", True):
            label = f"[节拍失准·无效] {arm['mode']} {arm['interval_ms']}ms: " \
                    f"fire {arm.get('fire_elapsed_ms')}ms ≠ 标称"
        if label:
            print("      " + label)
            deltas = arm.get("arrival_deltas") or {}
            if deltas:
                print(f"        到达间隔: min={deltas.get('min_ms')} "
                      f"med={deltas.get('median_ms')} "
                      f"max={deltas.get('max_ms')}ms "
                      f"(max/T={deltas.get('max_ms', 0) / arm['interval_ms']:.1f}倍)")


def build_parser():
    parser = argparse.ArgumentParser(
        description="触发间隔-轴速度-相机帧率关系测试（静态软触发+真实飞拍）")
    parser.add_argument("--config", default="gui/config.json")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--home", action="store_true",
                        help="明确授权本脚本执行真实回零与飞拍运动")
    parser.add_argument("--yes", action="store_true", help="跳过现场交互确认")
    parser.add_argument("--part", choices=["all", "a", "b"], default="all")
    parser.add_argument("--output-dir", default="output/trigger_rate")
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
    exposure_us = data.get("exposure_us", 700)
    gain_db = data.get("gain_db", 0.0)

    print("[TEST] 计划：")
    print("  A 静态软触发: 2模式 × {}档间隔({}-{}ms) × {}触发".format(
        len(PART_A_INTERVALS_MS), PART_A_INTERVALS_MS[-1],
        PART_A_INTERVALS_MS[0], PART_A_TRIGGERS))
    n_spot = len(SPOT_ARMS) * SPOT_REPS
    print("  B 飞拍: 精扫{}档变步距@300µm/s + 粗扫{}档 + 抽检{}臂×{}遍，"
          "窗口走廊[{},{}]µm".format(len(FINE_SWEEP_ARMS), len(COARSE_SWEEP_ARMS),
                                     len(SPOT_ARMS), SPOT_REPS,
                                     CORRIDOR_LO_UM, CORRIDOR_HI_UM))
    print("[REAL] 本脚本将执行回零、真实轴运动与真实采图")
    if not args.home:
        print("[STOP] 未提供--home，本脚本不会连接或产生真实运动")
        return 2
    if not args.yes:
        answer = input("确认机械区域安全、急停可用，并允许回零和真实运动。"
                       "请输入 RUN 继续: ")
        if answer.strip().upper() != "RUN":
            print("[CANCELLED] 用户未确认，未连接硬件")
            return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stamp}.json"
    results = []

    backend = LctMotionBackend(base_cfg)
    cam = HikCamera(args.camera_index)
    backend.connect()
    try:
        stroke_um = list(backend.read_stroke_range())  # 返回值已是 µm
        print(f"[INFO] 行程范围: {stroke_um}µm")
        if not (stroke_um[0] + 100 <= CORRIDOR_LO_UM
                and CORRIDOR_HI_UM + 100 <= stroke_um[1]):
            print(f"[STOP] 安全走廊 [{CORRIDOR_LO_UM},{CORRIDOR_HI_UM}]µm "
                  f"不在行程 {stroke_um}µm 内缩范围内，拒绝运动")
            return 2
        worst_pos_s = (stroke_um[1] - stroke_um[0]) / max(
            base_cfg.positioning_velocity_um_s, 1.0)
        if worst_pos_s > SCAN_TIMEOUT_S / 2:
            print(f"[STOP] 定位速度 {base_cfg.positioning_velocity_um_s}µm/s 下"
                  f"全程定位需 {worst_pos_s:.1f}s，超出扫描超时预算"
                  f"（{SCAN_TIMEOUT_S}s 的一半），请检查配置")
            return 2
        backend.home(timeout_s=base_cfg.home_timeout_s)
        cam.open()

        recorder = FrameRecorder()

        if args.part in ("all", "a"):
            run_part_a(cam, "fine", exposure_us, gain_db, results, out_path)
            run_part_a(cam, "coarse", exposure_us, gain_db, results, out_path)

        if args.part in ("all", "b"):
            # Part B：回调路径（注册后不可再调 get_frame）
            cam.register_frame_callback(recorder)
            cam.set_trigger_mode("hardware")
            cam.start_grabbing()
            try:
                # 按模式分组减少切换：coarse 探测+抽检 → fine 探测+抽检
                spot = [(m, s, v, sp) for m, s, v, sp in SPOT_ARMS]
                plan = ([("coarse", s, v, s * n, 1)
                         for s, v, n in COARSE_SWEEP_ARMS] +
                        [(m, s, v, sp, rep) for rep in range(1, SPOT_REPS + 1)
                         for m, s, v, sp in spot if m == "coarse"] +
                        [("fine", s, v, s * n, 1)
                         for s, v, n in FINE_SWEEP_ARMS] +
                        [(m, s, v, sp, rep) for rep in range(1, SPOT_REPS + 1)
                         for m, s, v, sp in spot if m == "fine"])
                current_mode = None
                for mode, step, vel, span, rep in plan:
                    if mode != current_mode:
                        apply_camera_mode(cam, mode, exposure_us, gain_db)
                        cam.set_trigger_mode("hardware")
                        cam.start_grabbing()
                        current_mode = mode
                    try:
                        run_motion_arm(backend, cam, mode, step, vel, span, rep,
                                       base_cfg.line_scan_overrun_um,
                                       recorder, results, out_path)
                    except Exception as exc:  # 运动层异常：伺服可能已被安全下电
                        results.append({"part": "B", "mode": mode,
                                        "step_um": step, "velocity_um_s": vel,
                                        "rep": rep, "error": repr(exc)})
                        write_json(out_path, results)
                        print(f"[B] 异常 {mode} {step}µm@{vel} r{rep}: {exc!r}，"
                              f"尝试重新回零恢复")
                        try:
                            backend.home(timeout_s=base_cfg.home_timeout_s)
                        except Exception as home_exc:
                            print(f"[STOP] 恢复失败: {home_exc!r}")
                            break
            finally:
                cam.stop_grabbing()
    finally:
        try:
            cam.close()
        finally:
            backend.disconnect()

    summarize(results)
    print(f"[OUTPUT] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
