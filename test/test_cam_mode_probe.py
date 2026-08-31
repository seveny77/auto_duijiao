# -*- coding: utf-8 -*-
"""相机模式探针 v2：测各采样形态的帧周期 P（决定 t_min 与 v_max）。

已定案（第一轮 + 本轮）：
  - 全幅与 4x4 decimation 帧周期同为 ~52.9ms（相机自报 ResultingFrameRate=18.9fps，
    传感器读出受限，ISP 侧抽取不提速）；
  - 触发闩锁深度=1，丢帧条件 (k-2)(P-T)>=T。
本轮补测：
  - 小 ROI（生产精扫形态 604×620）的 P → 精扫 v_max；
  - binning 4x4（若片上合并则读出缩短）的 P → 粗扫提速空间；
  - 2x2 decimation 的 P。
测量方式：软触发 12 发（20ms 间隔）+ 并发排水（避免节点池=1 丢帧干扰），
交付帧尺寸 + 到达间隔即 P（T<P 时到达间隔=P）。

用法：
  python test/test_cam_mode_probe.py --yes
"""

import argparse
import statistics
import sys
import threading
import time

from ctypes import c_bool

from camera import HikCamera
from backend.camera_utils import set_full_frame, set_coarse_frame
from MvImport.CameraParams_header import MVCC_INTVALUE, MVCC_FLOATVALUE

FIRES = 12
FIRE_INTERVAL_S = 0.020
DRAIN_TIMEOUT_MS = 200
DRAIN_IDLE_S = 0.6
DRAIN_CAP_S = 5.0
SENSOR_FULL = (5472, 3648)


def read_nodes(cam):
    """读回相机关键节点，返回 dict（读取失败的节点记 None）。"""

    out = {}
    sdk = cam._cam  # 探针脚本允许触达私有句柄
    for key in ("DecimationHorizontal", "DecimationVertical",
                "BinningHorizontal", "BinningVertical",
                "Width", "Height", "OffsetX", "OffsetY"):
        info = MVCC_INTVALUE()
        try:
            ret = sdk.MV_CC_GetIntValueEx(key, info)
            out[key] = int(info.nCurValue) if ret == 0 else None
        except Exception:
            out[key] = None
    f = MVCC_FLOATVALUE()
    for name in ("ExposureTime", "ResultingFrameRate"):
        try:
            ret = sdk.MV_CC_GetFloatValue(name, f)
            out[name] = round(f.fCurValue, 2) if ret == 0 else None
        except Exception:
            out[name] = None
    b = c_bool()
    try:
        ret = sdk.MV_CC_GetBoolValue("AcquisitionFrameRateEnable", b)
        out["AcqRateEnable"] = bool(b.value) if ret == 0 else None
    except Exception:
        out["AcqRateEnable"] = None
    return out


def measure(cam):
    """软触发连发 + 并发排水：返回 (帧数, 尺寸集合, 间隔ms统计)。"""

    stop = threading.Event()
    frames = []
    arrivals = []

    def drain():
        while not stop.is_set():
            try:
                img = cam.get_frame(DRAIN_TIMEOUT_MS)
            except Exception:
                img = None
            if img is not None:
                frames.append(img.shape)
                arrivals.append(time.perf_counter())

    cam.set_trigger_mode("software")
    cam.start_grabbing()
    t = threading.Thread(target=drain, daemon=True)
    t.start()
    t0 = time.perf_counter()
    try:
        for i in range(FIRES):
            if i > 0:
                deadline = t0 + i * FIRE_INTERVAL_S
                while time.perf_counter() < deadline:
                    pass  # 纯自旋，避开 Windows sleep 粒度
            cam.trigger_software()
        fire_elapsed = time.perf_counter() - t0
    finally:
        stop.set()
        t.join(2.0)
        if t.is_alive():
            raise RuntimeError("排水线程未随发令结束退出")
    # 排空尾部在途帧
    last_change = time.perf_counter()
    while time.perf_counter() - last_change < DRAIN_IDLE_S and \
            time.perf_counter() - t0 < DRAIN_CAP_S:
        try:
            img = cam.get_frame(DRAIN_TIMEOUT_MS)
        except Exception:
            img = None
        if img is not None:
            frames.append(img.shape)
            arrivals.append(time.perf_counter())
            last_change = time.perf_counter()
    cam.stop_grabbing()
    arrivals.sort()
    deltas = [(b - a) * 1000 for a, b in zip(arrivals, arrivals[1:])]
    stat = {}
    if deltas:
        stat = {"min": round(min(deltas), 1),
                "med": round(statistics.median(deltas), 1),
                "max": round(max(deltas), 1)}
    return len(frames), sorted(set(frames)), stat, \
        round(fire_elapsed * 1000, 1)


def apply_and_measure(cam, name, apply_fn):
    print(f"\n=== 序列 [{name}] ===")
    cam.stop_grabbing()
    apply_fn(cam)
    nodes = read_nodes(cam)
    print("  节点读回:", nodes)
    n, shapes, stat, fire_ms = measure(cam)
    print(f"  交付帧: {n}/{FIRES} 帧, 尺寸集合: {shapes}")
    print(f"  到达间隔ms: {stat}  发令耗时: {fire_ms}ms "
          f"(标称 {FIRES * FIRE_INTERVAL_S * 1000:.0f}ms)")
    return {"name": name, "nodes": nodes, "frames": n,
            "shapes": [list(s) for s in shapes], "deltas": stat,
            "fire_ms": fire_ms}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true",
                    help="跳过交互确认（相机占用需先关GUI）")
    args = ap.parse_args()
    if not args.yes:
        ans = input("相机探针将占用 USB 相机（不动轴），确认请输入 yes: ")
        if ans.strip().lower() != "yes":
            print("已取消")
            return 2

    cam = HikCamera(0)
    cam.open()
    results = []
    try:
        # 1) 全幅基线
        results.append(apply_and_measure(
            cam, "full_1x1",
            lambda c: set_full_frame(c, 1)))
        # 2) 生产粗扫：decimation 4x4
        results.append(apply_and_measure(
            cam, "decim4",
            lambda c: set_coarse_frame(c, "decimation", 4)))
        # 3) binning 4x4（若片上合并，读出行数减少）
        results.append(apply_and_measure(
            cam, "binning4",
            lambda c: set_coarse_frame(c, "binning", 4)))
        # 4) decimation 2x2
        results.append(apply_and_measure(
            cam, "decim2",
            lambda c: set_coarse_frame(c, "decimation", 2)))
        # 5) 生产精扫形态：全幅1x1 → YOLO 小 ROI（必须先复位降采样，
        #    否则 OffsetX 超出降采样坐标系上限，0x80000102）
        def _fine_roi(c):
            set_full_frame(c, 1)
            c.set_roi(2540, 1192, 604, 620)
        results.append(apply_and_measure(
            cam, "fine_roi_604x620", _fine_roi))
    finally:
        try:
            cam.stop_grabbing()
        except Exception:
            pass
        cam.close()

    print("\n===== 对比汇总 =====")
    print(f"{'序列':18s} {'Dec':5s} {'Bin':5s} {'节点WxH':12s} "
          f"{'交付尺寸':14s} {'帧数':6s} {'P(ms)':8s} {'相机自报fps':10s}")
    for r in results:
        nd = r["nodes"]
        dec = f"{nd.get('DecimationHorizontal')}/{nd.get('DecimationVertical')}"
        bin_ = f"{nd.get('BinningHorizontal')}/{nd.get('BinningVertical')}"
        wh = f"{nd.get('Width')}x{nd.get('Height')}"
        shapes = ",".join(f"{s[1]}x{s[0]}" for s in r["shapes"]) or "-"
        med = r["deltas"].get("med", "-")
        fps = nd.get("ResultingFrameRate", "-")
        print(f"{r['name']:18s} {dec:5s} {bin_:5s} {wh:12s} {shapes:14s} "
              f"{r['frames']:<4d}/12 {str(med):8s} {str(fps):10s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
