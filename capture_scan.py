# -*- coding: utf-8 -*-
"""全图采集：Z轴等步长飞拍，逐帧保存JPG，供离线标定使用。

真实模式会自动读取相机最大传感器尺寸，并根据 --dec 设置
Decimation。--dec 1 表示传感器全幅，2/4 表示对应降采样倍数。

用法：
    python capture_scan.py --out-dir D:\\scan --dec 2 --yes
"""

import argparse
import os
import queue
import threading
import time
from typing import List, Optional, Tuple

import cv2
# 12M:4096x3000
from backend.camera_utils import set_coarse_frame
# ============================================================
# 帧收集器：回调入队 → 工作线程逐帧保存 JPG
# ============================================================
class CaptureCollector:
    """相机回调把 (图像, 序号) 入队；工作线程取出后保存为 JPG。

    序号 = 帧到达顺序（0,1,2,...），即位置顺序（含尾不含首）。
    队列满时丢帧（由张数校验发现，不阻塞 SDK 回调线程）。
    """

    def __init__(self, camera, out_dir, start_index=0, max_queue=64, quality=95):
        self._cam = camera
        self._out_dir = out_dir
        self._start_index = start_index
        self._quality = quality
        self._queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._enqueued = 0
        self._processed = 0
        self._save_times: List[float] = []
        self._error = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        os.makedirs(out_dir, exist_ok=True)


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
                if self._error is not None:
                    return False
                if self._processed >= count:
                    return True
            time.sleep(0.02)
        return False

    def queue_empty(self) -> bool:
        return self._queue.empty()

    def save_stats(self) -> dict:
        with self._lock:
            times = list(self._save_times)
        if not times:
            return {"count": 0, "avg_ms": 0.0, "total_ms": 0.0}
        return {
            "count": len(times),
            "avg_ms": sum(times) / len(times),
            "total_ms": sum(times),
        }

    def _on_frame(self, img):
        seq = self._enqueued
        self._enqueued += 1
        try:
            self._queue.put_nowait((img, seq))
        except queue.Full:
            pass

    def _worker(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:
                img, seq = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                path = os.path.join(self._out_dir, f"img_{seq + self._start_index:04d}.jpg")
                t0 = time.perf_counter()
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
                if not ok:
                    raise RuntimeError(f"JPG 编码失败 seq={seq}")
                buf.tofile(path)
                dt = (time.perf_counter() - t0) * 1000
                with self._lock:
                    self._processed += 1
                    self._save_times.append(dt)
            except Exception as e:
                with self._lock:
                    self._error = f"第 {seq} 帧保存失败: {e}"
                self._stop.set()


# ============================================================
# 参数
# ============================================================
def compute_capture_params(
    stroke_min: int, stroke_max: int, step_um: int
) -> Tuple[int, int, int, int]:
    """返回 (飞拍起点, 终点, 步距, 预期帧数)。含尾不含首。"""
    if step_um <= 0:
        raise ValueError(f"步距必须 > 0: {step_um}")
    if stroke_max <= stroke_min:
        raise ValueError(f"行程无效: min={stroke_min}, max={stroke_max}")
    n = (stroke_max - stroke_min) // step_um
    if n < 1:
        raise ValueError("张数 < 1，请检查行程/步距")
    fly_start = stroke_min
    fly_end = stroke_min + n * step_um
    return fly_start, fly_end, step_um, n


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="全图采集（等步长飞拍 → JPG）")
    p.add_argument("--out-dir", required=True, help="JPG 保存目录")
    p.add_argument("--mode", choices=["real", "sim"], default="real")
    p.add_argument("--stroke-min", type=int, default=11700)
    p.add_argument("--stroke-max", type=int, default=11900)
    p.add_argument("--step-um", type=int, default=5)
    p.add_argument("--start-index", type=int, default=0, help="文件命名起始编号（第一张 = start-index）")
    p.add_argument("--exposure-us", type=int, default=14364)
    p.add_argument("--gain-db", type=float, default=0.0)
    p.add_argument("--plc-host", default="192.168.100.88")
    p.add_argument("--plc-port", type=int, default=502)
    p.add_argument("--camera-index", type=int, default=0)
    p.add_argument("--flyscan-timeout", type=float, default=600.0)
    p.add_argument("--frame-wait-timeout", type=float, default=60.0)
    p.add_argument("--yes", action="store_true", help="跳过飞拍确认")
    p.add_argument("--dec", type=int, choices=[1, 2, 4], default=1,
                   help="降采样倍数（decimation 1/2/4，1=全幅）")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    fly_start, fly_end, step_um, n = compute_capture_params(
        args.stroke_min, args.stroke_max, args.step_um
    )
    print(f"采集参数: 起点={fly_start} 终点={fly_end} 步距={step_um}μm → {n} 帧")
    print(f"保存目录: {os.path.abspath(args.out_dir)}")

    if args.mode == "sim":
        from autofocus_sim import FakePlcClient, SimCamera

        plc = FakePlcClient(args.stroke_min, args.stroke_max, n)
        cam = SimCamera(n=n, interval_s=0.001)
    else:
        from plc.client import PlcClient
        from camera import HikCamera

        plc = PlcClient(args.plc_host, args.plc_port, timeout=5.0)
        cam = HikCamera(args.camera_index)

    collector = None
    try:
        if args.mode == "real":
            last_err = None
            for attempt in range(3):
                try:
                    plc.connect()
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    print(f"PLC 连接第{attempt+1}次失败: {e}")
                    time.sleep(2)
            if last_err:
                raise last_err

        if args.mode == "real":
            cam.open()
            cam.set_exposure(args.exposure_us)
            cam.set_gain(args.gain_db)
            sensor_width, sensor_height = set_coarse_frame(
                cam,
                mode="decimation",
                factor=args.dec,
            )

            capture_width = (sensor_width // args.dec) // 4 * 4
            capture_height = (sensor_height // args.dec) // 4 * 4

            print(
                f"传感器={sensor_width}x{sensor_height}, "
                f"降采样 dec={args.dec} → "
                f"{capture_width}x{capture_height}"
            )
        else:
            cam.open()

        if args.mode == "real" and not args.yes:
            ans = input("⚠️  即将触发飞拍（Z 轴会运动），确认请输入 yes: ")
            if ans.strip().lower() != "yes":
                print("用户取消")
                return 1

        collector = CaptureCollector(cam, args.out_dir, start_index=args.start_index)
        collector.start()

        t0 = time.perf_counter()
        count = plc.flyscan_trigger(
            fly_start, fly_end, step_um, timeout_s=args.flyscan_timeout
        )
        flyscan_s = time.perf_counter() - t0

        if not collector.wait(count, timeout=args.frame_wait_timeout):
            err = collector.error
            raise RuntimeError(
                err or f"帧处理超时: 已处理 {collector.processed}/{count}"
            )

        # 稳定期：处理数达标且队列清空
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if collector.processed > count or (
                collector.processed == count and collector.queue_empty()
            ):
                break
            time.sleep(0.02)
        processed = collector.processed
        if processed != count:
            raise RuntimeError(f"帧数不符: 处理 {processed}, PLC 返回 {count}")

        stats = collector.save_stats()
        print(f"PLC 返回张数: {count}")
        print(f"已保存 {processed} 张 → {os.path.abspath(args.out_dir)}")
        print(f"飞拍耗时: {flyscan_s*1000:.0f} ms | 平均保存: {stats['avg_ms']:.2f} ms/张")
        return 0
    except Exception as e:
        print(f"[错误] {e}")
        return 1
    finally:
        if collector is not None:
            collector.stop()
        try:
            cam.close()
        except Exception:
            pass
        try:
            plc.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
