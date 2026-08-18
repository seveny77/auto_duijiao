# -*- coding: utf-8 -*-
"""自动对焦闭环 v1（控制台版）：全扫 + 定点回位

流程:
  1. VA 连接 PLC，读取丝杆行程 (μm)
  2. 打开相机（硬触发 LINE0），回调把图像入队，工作线程做 Tenengrad 评价
  3. VA 发送等步长飞拍:
       步距 = round((行程max - 行程min) / N)   # 起点不纳入，共 N 帧
       飞拍起点 = 行程min                      # PLC 缺首：移动一步后才触发第一帧
       终点 = 行程min + N * 步距
     PLC 连续运动 + 硬触发拍照，内部记录每次触发的索引与真实位置
  4. 飞拍完成后，软件按帧到达顺序映射 index 0..N-1，取清晰度最高 index
  5. VA 发送 move_to_position(最佳 index)（PLC 内部换算位置并拍照）
  6. VA 发送 process_complete，结束本轮

用法:
  python autofocus_controller.py                    # 真机联调
  python autofocus_controller.py --mode sim         # 用 _scores.json 模拟
  python autofocus_controller.py --mode sim --yes   # 跳过确认
"""

import argparse
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from focus_template import FocusTemplate


class AutofocusError(Exception):
    """自动对焦流程致命错误。"""


# ============================================================
# 配置
# ============================================================
@dataclass
class AutofocusConfig:
    mode: str = "real"                       # real | sim
    plc_host: str = "192.168.100.88"
    plc_port: int = 502
    plc_timeout: float = 5.0
    camera_index: int = 0
    template_path: str = "data/template.json"
    roi: Optional[Tuple[int, int, int, int]] = (840, 500, 700, 700)   # (x, y, w, h)，None=模板 meta
    num_images: int = 0                      # 0=模板 total_images
    step_um: Optional[int] = None            # 直接指定步距（与 num_images 互斥）
    exposure_us: int = 400
    gain_db: float = 0.0
    binning: int = 2
    flyscan_timeout_s: float = 600.0
    frame_wait_timeout_s: float = 30.0
    stroke_min_um: Optional[int] = 11000      # 覆盖 PLC 行程（PLC 读到 0 或联调时用）
    stroke_max_um: Optional[int] = 12000
    sim_scores_path: str = "_scores.json"
    skip_confirm: bool = False               # 真机飞拍前跳过确认
    final_image_path: str = "final_image.png"     # 定点拍照图保存路径
    final_frame_timeout_s: float = 3.0            # 等待定点拍照帧超时
    hardware_roi: bool = False                    # 硬件开窗采集（评价对开窗后全图）


# ============================================================
# 帧收集器：相机回调 → 队列；工作线程逐帧评价，按到达顺序编号
# ============================================================
class FrameCollector:
    """相机回调把 (图像, 序号) 入队；工作线程取出后评价，保存 scores[seq]。

    序号 = 帧到达顺序（0,1,2,...），与 PLC 触发顺序一一对应。
    队列满时丢帧（由张数校验发现，不阻塞 SDK 回调线程）。
    """

    def __init__(self, camera, evaluator, roi, max_queue=64):
        self._cam = camera
        self._eval = evaluator
        self._roi = roi
        self._queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._enqueued = 0
        self._processed = 0
        self._scores: Dict[int, float] = {}
        self._eval_times: List[float] = []
        self._last_image = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    # ---------- 生命周期 ----------
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

    # ---------- 查询 ----------
    def wait(self, count: int, timeout: float) -> bool:
        """等待已处理帧数 >= count，超时返回 False。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._processed >= count:
                    return True
            time.sleep(0.02)
        return False

    @property
    def processed(self) -> int:
        with self._lock:
            return self._processed

    def scores(self) -> Dict[int, float]:
        with self._lock:
            return dict(self._scores)

    @property
    def last_image(self):
        """最近一帧图像（评价成功的最新帧）。"""
        with self._lock:
            return self._last_image

    def queue_empty(self) -> bool:
        """工作线程待处理队列是否已空。"""
        return self._queue.empty()

    def eval_stats(self) -> dict:
        """每帧评价耗时统计（ms）：count/total/avg/min/max。"""
        with self._lock:
            times = list(self._eval_times)
        if not times:
            return {"count": 0, "total_ms": 0.0, "avg_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}
        return {
            "count": len(times),
            "total_ms": sum(times),
            "avg_ms": sum(times) / len(times),
            "min_ms": min(times),
            "max_ms": max(times),
        }

    # ---------- 内部 ----------
    def _on_frame(self, img):
        """SDK 回调线程：只入队，不评价。"""
        seq = self._enqueued
        self._enqueued += 1
        try:
            self._queue.put_nowait((img, seq))
        except queue.Full:
            pass

    def _worker(self):
        """工作线程：取帧 → 评价 → 存分数。"""
        while not self._stop.is_set() or not self._queue.empty():
            try:
                img, seq = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                t_eval = time.perf_counter()
                score = self._eval.evaluate_image(img, self._roi)
                dt_ms = (time.perf_counter() - t_eval) * 1000
                with self._lock:
                    self._scores[seq] = score
                    self._processed += 1
                    self._eval_times.append(dt_ms)
                    self._last_image = img
            except Exception as e:
                print(f"[错误] 第 {seq} 帧评价失败: {e}")


# ============================================================
# 控制器
# ============================================================
class AutofocusController:
    """把 PLC 飞拍、相机取流、清晰度评价、回位串成一轮全扫自动对焦。"""

    def __init__(self, config: AutofocusConfig, plc=None, camera=None, evaluator=None):
        self.cfg = config
        # 允许测试注入替身；None 时按 mode 创建
        self._plc = plc
        self._cam = camera
        self._evaluator = evaluator
        self._collector: Optional[FrameCollector] = None

    # ---------- 硬件创建 ----------
    def _create_hardware(self):
        if self.cfg.mode == "sim":
            from autofocus_sim import FakePlcClient, ScoreMapEvaluator, SimCamera

            scores = self._load_sim_scores()
            stroke_min = self.cfg.stroke_min_um or 11000
            stroke_max = self.cfg.stroke_max_um or 12000
            if self.cfg.step_um:
                n = (stroke_max - stroke_min) // self.cfg.step_um
            else:
                n = self._resolve_num_images()
            plc = FakePlcClient(stroke_min, stroke_max, n)
            cam = SimCamera(n=n, interval_s=0.002)
            evaluator = ScoreMapEvaluator(scores)
            return plc, cam, evaluator

        from plc.client import PlcClient
        from camera import HikCamera
        from adapters.evaluator_opencv import OpenCVSharpnessEvaluator

        plc = PlcClient(self.cfg.plc_host, self.cfg.plc_port, self.cfg.plc_timeout)
        cam = HikCamera(self.cfg.camera_index)
        evaluator = OpenCVSharpnessEvaluator()
        return plc, cam, evaluator

    def _load_sim_scores(self) -> List[float]:
        import json
        import os

        path = self.cfg.sim_scores_path
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
        with open(path, "r", encoding="utf-8-sig") as f:
            scores = json.load(f)
        return [float(s) for s in scores]

    def _resolve_num_images(self) -> int:
        if self.cfg.num_images and self.cfg.num_images > 1:
            return self.cfg.num_images
        template = FocusTemplate.load(self.cfg.template_path)
        n = int(template.meta.get("total_images", 0))
        if n < 2:
            raise AutofocusError("模板 total_images 无效，请用 --num-images 指定")
        return n

    def _resolve_roi(self) -> Optional[Tuple[int, int, int, int]]:
        if self.cfg.roi:
            return self.cfg.roi
        template = FocusTemplate.load(self.cfg.template_path)
        roi = template.meta.get("roi")
        if roi and len(roi) == 4:
            return tuple(int(v) for v in roi)
        return None

    # ---------- 飞拍参数 ----------
    @staticmethod
    def _align_window(
        roi: Tuple[int, int, int, int],
        sensor_size: Tuple[int, int] = (2600, 2160),
        inc: int = 4,
    ) -> Tuple[int, int, int, int]:
        """硬件开窗前校验/对齐：Offset 与宽高必须是 inc 的倍数且不越界。
        不对齐时向下对齐并打印警告；越界时报错。"""
        x, y, w, h = roi
        if any(v % inc for v in (x, y, w, h)):
            ax, ay, aw, ah = (v - v % inc for v in (x, y, w, h))
            print(f"[警告] 开窗参数需 {inc} 对齐: ({x},{y},{w},{h}) -> ({ax},{ay},{aw},{ah})")
            x, y, w, h = ax, ay, aw, ah
        sw, sh = sensor_size
        if w < inc or h < inc or x < 0 or y < 0 or x + w > sw or y + h > sh:
            raise AutofocusError(f"开窗越界: ({x},{y},{w},{h}) 超出传感器 {sw}x{sh}")
        return (x, y, w, h)

    @staticmethod
    def compute_flyscan_params(
        stroke_min: int,
        stroke_max: int,
        n: int = 0,
        step_um: Optional[int] = None,
    ) -> Tuple[int, int, int]:
        """起点不纳入计算（PLC 缺首：起点不触发，移动一步后拍第一帧）：
        飞拍起点 = 行程min，终点 = 行程min + N×步距。

        - N 驱动（step_um=None）：step = max(1, round(跨度/N))，
          若 行程min + N×step 超出行程上限，回退 step = 跨度//N。
        - 步距驱动（step_um 给定）：N = 跨度//step（向下取整，不超行程）。

        返回 (飞拍起点, 终点, 步距)；预期 PLC 张数 = (终点-飞拍起点)//步距
        （缺首：不含起点）。
        """
        if stroke_max <= stroke_min:
            raise AutofocusError(f"行程无效: min={stroke_min}, max={stroke_max}")
        span = stroke_max - stroke_min

        if step_um is not None:
            if step_um <= 0:
                raise AutofocusError(f"步距必须 > 0: {step_um} μm")
            n = span // step_um
            step = step_um
            if n < 2:
                raise AutofocusError(
                    f"行程 {span} μm / 步距 {step_um} μm 只能拍 {n} 张，步距过大"
                )
        else:
            if n < 2:
                raise AutofocusError("N 必须 >= 2")
            step = max(1, round(span / n))
            if stroke_min + n * step > stroke_max:
                step = span // n
            if step < 1:
                raise AutofocusError(f"步距 <= 0: {step} μm（行程太小或 N 太大）")

        end_um = stroke_min + n * step
        flyscan_start_um = stroke_min
        return flyscan_start_um, end_um, step

    # ---------- 主流程 ----------
    def run(self) -> dict:
        t0 = time.perf_counter()

        if self.cfg.step_um is not None and self.cfg.num_images > 0:
            raise AutofocusError("--step-um 与 --num-images 不能同时使用")

        if self._plc is None or self._cam is None or self._evaluator is None:
            self._plc, self._cam, self._evaluator = self._create_hardware()
        plc, cam, evaluator = self._plc, self._cam, self._evaluator

        roi = self._resolve_roi()

        plc.connect()
        print(f"PLC 已连接: {self.cfg.plc_host}:{self.cfg.plc_port}")

        # 行程：PLC 寄存器优先，0 或显式覆盖时用配置
        stroke_min, stroke_max = plc.read_stroke_range()
        if not stroke_min or not stroke_max:
            print("[警告] PLC 行程读取为 0，使用配置/默认行程")
            stroke_min = self.cfg.stroke_min_um or 11000
            stroke_max = self.cfg.stroke_max_um or 12000
        if self.cfg.stroke_min_um is not None:
            stroke_min = self.cfg.stroke_min_um
        if self.cfg.stroke_max_um is not None:
            stroke_max = self.cfg.stroke_max_um

        if self.cfg.step_um is not None:
            fly_start_um, fly_end_um, step_um = self.compute_flyscan_params(
                stroke_min, stroke_max, step_um=self.cfg.step_um
            )
            expected_n = (fly_end_um - fly_start_um) // step_um
        else:
            n = self._resolve_num_images()
            fly_start_um, fly_end_um, step_um = self.compute_flyscan_params(
                stroke_min, stroke_max, n
            )
            expected_n = n
        print(f"行程: {stroke_min} ~ {stroke_max} μm, N={expected_n}, 步距={step_um} μm")
        print(f"飞拍范围: {fly_start_um} -> {fly_end_um} μm（起点不拍，共 {expected_n} 帧）")

        if self.cfg.mode == "real":
            cam.open()
            cam.set_exposure(self.cfg.exposure_us)
            cam.set_gain(self.cfg.gain_db)
            cam.set_binning(self.cfg.binning, self.cfg.binning)
            if self.cfg.hardware_roi and roi:
                x, y, w, h = self._align_window(roi)
                cam.set_roi(x, y, w, h)
                print(f"硬件开窗: ({x},{y}) {w}x{h}，评价对开窗后全图进行")
        else:
            cam.open()

        if self.cfg.mode == "real" and not self.cfg.skip_confirm:
            answer = input("⚠️  即将触发 PLC 飞拍（Z 轴会运动），确认请输入 yes: ")
            if answer.strip().lower() != "yes":
                raise AutofocusError("用户取消飞拍")

        # 开始取流（硬触发，PLC 脉冲来帧）
        # 硬件开窗模式下帧本身就是窗口，评价整帧；否则按 ROI 软件裁剪评价
        eval_roi = None if self.cfg.hardware_roi else roi
        collector = FrameCollector(cam, evaluator, eval_roi)
        self._collector = collector
        collector.start()

        # 第一阶段计时：从发出飞拍指令开始
        t_phase1 = time.perf_counter()
        plc_count = plc.flyscan_trigger(
            start_pos_um=fly_start_um,
            end_pos_um=fly_end_um,
            step_um=step_um,
            timeout_s=self.cfg.flyscan_timeout_s,
        )
        flyscan_s = time.perf_counter() - t_phase1
        print(f"PLC 飞拍完成: 返回张数 {plc_count}, 耗时 {flyscan_s*1000:.0f} ms")
        if plc_count != expected_n:
            print(f"[警告] PLC 返回 {plc_count} 帧, 预期 {expected_n} 帧，按 PLC 实际张数处理")

        # 等处理线程评完 PLC 返回的张数
        t_wait = time.perf_counter()
        if not collector.wait(plc_count, self.cfg.frame_wait_timeout_s):
            processed = collector.processed
            raise AutofocusError(
                f"帧处理超时: 已处理 {processed}/{plc_count} 帧"
            )
        # 稳定期：等处理帧数稳定（0.2s 无新帧）再严格比对张数，
        # 避免"相机还在出帧但队列恰好为空"时提前通过，漏掉多余帧。
        settle_deadline = time.monotonic() + 1.0
        last_processed = -1
        stable_since = time.monotonic()
        while time.monotonic() < settle_deadline:
            now = collector.processed
            if now != last_processed:
                last_processed = now
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 0.2:
                break
            time.sleep(0.02)
        processed = collector.processed
        if processed != plc_count:
            raise AutofocusError(
                f"帧数不符: 处理 {processed} 帧, PLC 返回 {plc_count} 帧"
            )
        wait_s = time.perf_counter() - t_wait
        eval_stats = collector.eval_stats()

        scores = collector.scores()
        score_list = [scores.get(i) for i in range(plc_count)]
        missing = [i for i, s in enumerate(score_list) if s is None]
        if missing:
            raise AutofocusError(
                f"丢帧 {len(missing)} 张（缺失序号 {missing[:10]}...），"
                f"PLC 张数 {plc_count} 与处理帧数不符"
            )

        # 全扫取最高分
        best_index = max(range(plc_count), key=lambda i: score_list[i])
        best_score = score_list[best_index]

        # 回位 + 结束（PLC 定点表 1 起始：帧序号 k ↔ PLC index k+1，index 0 无效）
        move_index = best_index + 1
        target_um = stroke_min + move_index * step_um
        print(f"回位 PLC index={move_index}（帧序 {best_index}，≈{target_um} μm）")
        # 第二阶段计时：从发出回位指令开始
        t_phase2 = time.perf_counter()
        plc.move_to_position(move_index)
        move_s = time.perf_counter() - t_phase2
        ct_s = time.perf_counter() - t_phase1
        plc.process_complete()

        # 保存定点拍照图（PLC 移动+拍照产生的最后一帧）
        final_score = None
        saved = False
        if self.cfg.mode == "real":
            deadline = time.monotonic() + self.cfg.final_frame_timeout_s
            while time.monotonic() < deadline:
                if collector.processed > plc_count:
                    break
                time.sleep(0.02)
            final_img = collector.last_image if collector.processed > plc_count else None
            if final_img is None:
                print("[警告] 未收到定点拍照帧，跳过保存定点图")
            else:
                self._save_image(final_img, self.cfg.final_image_path)
                final_score = collector.scores().get(plc_count)
                saved = True
                print(f"定点拍照图已保存: {self.cfg.final_image_path}  score={final_score:.2f}")

        total_s = time.perf_counter() - t0
        report = {
            "mode": self.cfg.mode,
            "plc_host": self.cfg.plc_host,
            "stroke_min_um": stroke_min,
            "stroke_max_um": stroke_max,
            "num_images": expected_n,
            "step_um": step_um,
            "flyscan": (fly_start_um, fly_end_um, step_um),
            "plc_count": plc_count,
            "processed": len(scores),
            "best_index": best_index,
            "move_index": move_index,
            "best_score": best_score,
            "flyscan_s": flyscan_s,
            "eval_stats": eval_stats,
            "evaluate_wait_s": wait_s,
            "move_s": move_s,
            "ct_s": ct_s,
            "total_s": total_s,
            "final_image_saved": saved,
            "final_image_score": final_score,
            "final_image_path": self.cfg.final_image_path,
            "hardware_roi": self.cfg.hardware_roi,
        }
        self._print_report(report)
        return report

    @staticmethod
    def _save_image(img, path: str):
        """保存 BGR 图像为 PNG（用 imencode 绕开中文路径问题）。"""
        import os
        import cv2

        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise AutofocusError(f"图像编码失败: {path}")
        buf.tofile(path)

    def close(self):
        if self._collector is not None:
            self._collector.stop()
            self._collector = None
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception as e:
                print(f"[警告] 相机关闭失败: {e}")
        if self._plc is not None:
            try:
                self._plc.disconnect()
            except Exception as e:
                print(f"[警告] PLC 断开失败: {e}")

    def _print_report(self, r: dict):
        print("\n" + "=" * 56)
        print("  自动对焦报告（全扫）")
        print("=" * 56)
        print(f"  模式:          {r['mode']}")
        print(f"  PLC:           {r['plc_host']}")
        print(f"  行程:          {r['stroke_min_um']} ~ {r['stroke_max_um']} μm")
        print(f"  总张数 N:      {r['num_images']}   飞拍步距: {r['step_um']} μm")
        print(f"  飞拍范围:      {r['flyscan'][0]} -> {r['flyscan'][1]} μm")
        print(f"  PLC 张数:      {r['plc_count']}")
        print(f"  处理帧数:      {r['processed']}")
        print(f"  最佳 index:    {r['best_index']}   score={r['best_score']:.2f}")
        print(f"  回位 PLC index: {r['move_index']}")
        print(f"  ── 第一阶段（自飞拍指令起）──")
        print(f"  机构飞拍:      {r['flyscan_s']*1000:.0f} ms")
        es = r["eval_stats"]
        print(
            f"  单帧处理:      平均 {es['avg_ms']:.2f} ms | "
            f"最小 {es['min_ms']:.2f} | 最大 {es['max_ms']:.2f} "
            f"（{es['count']} 帧，共 {es['total_ms']:.0f} ms）"
        )
        print(f"  飞拍后等待:    {r['evaluate_wait_s']*1000:.0f} ms")
        print(f"  ── 第二阶段 ──")
        print(f"  机构运动到拍照: {r['move_s']*1000:.0f} ms")
        print(f"  CT（飞拍指令→回位完成）: {r['ct_s']*1000:.0f} ms")
        print(f"  整程序耗时:    {r['total_s']*1000:.0f} ms")
        if r["final_image_saved"]:
            print(f"  定点拍照图:    已保存 {r['final_image_path']}  score={r['final_image_score']:.2f}")
        else:
            print("  定点拍照图:    未保存")
        print(f"  采图方式:      {'硬件开窗（评价全图）' if r['hardware_roi'] else '全帧采集（软件ROI评价）'}")
        print("=" * 56)


# ============================================================
# CLI
# ============================================================
def _parse_roi(text: str) -> Tuple[int, int, int, int]:
    parts = [int(x) for x in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("ROI 格式: x,y,w,h")
    return tuple(parts)  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="自动对焦闭环 v1（全扫）")

    p.add_argument("--mode", choices=["real", "sim"], default="real")
    p.add_argument("--plc-host", default="192.168.100.88")
    p.add_argument("--plc-port", type=int, default=502)
    p.add_argument("--camera-index", type=int, default=0)
    p.add_argument("--template", default="data/template.json")
    p.add_argument("--roi", type=_parse_roi, default=None, help="x,y,w,h（默认取模板 meta）")
    p.add_argument("--num-images", type=int, default=0, help="全扫张数（默认取模板 total_images）")
    p.add_argument("--step-um", type=int, default=None, help="飞拍步距 μm（与 --num-images 互斥）")
    p.add_argument("--exposure-us", type=int, default=None)
    p.add_argument("--gain-db", type=float, default=0.0)
    p.add_argument("--binning", type=int, default=None, help="1|2|4（默认 1）")
    p.add_argument("--flyscan-timeout", type=float, default=600.0)
    p.add_argument("--frame-wait-timeout", type=float, default=30.0)
    p.add_argument("--stroke-min-um", type=int, default=None)
    p.add_argument("--stroke-max-um", type=int, default=None)
    p.add_argument("--sim-scores", default="_scores.json")
    p.add_argument("--yes", action="store_true", help="真机飞拍前跳过确认")
    p.add_argument("--final-image-path", default="final_image.png", help="定点拍照图保存路径")
    p.add_argument("--final-frame-timeout", type=float, default=3.0, help="等待定点拍照帧超时 s")
    p.add_argument("--hardware-roi", action="store_true", help="硬件开窗采集（评价对开窗后全图）")
    return p

def config_from_args(args) -> AutofocusConfig:
    """仅当命令行显式传参时才覆盖 dataclass 默认值。"""
    kwargs = dict(
        mode=args.mode,
        plc_host=args.plc_host,
        plc_port=args.plc_port,
        camera_index=args.camera_index,
        template_path=args.template,
        num_images=args.num_images,
        step_um=args.step_um,
        gain_db=args.gain_db,
        flyscan_timeout_s=args.flyscan_timeout,
        frame_wait_timeout_s=args.frame_wait_timeout,
        sim_scores_path=args.sim_scores,
        skip_confirm=args.yes,
        final_image_path=args.final_image_path,
        final_frame_timeout_s=args.final_frame_timeout,
        hardware_roi=args.hardware_roi,
    )
    if args.roi is not None:
        kwargs["roi"] = args.roi
    if args.binning is not None:
        kwargs["binning"] = args.binning
    if args.exposure_us is not None:
        kwargs["exposure_us"] = args.exposure_us
    if args.stroke_min_um is not None:
        kwargs["stroke_min_um"] = args.stroke_min_um
    if args.stroke_max_um is not None:
        kwargs["stroke_max_um"] = args.stroke_max_um
    return AutofocusConfig(**kwargs)


def main(argv=None) -> int:


    args = build_parser().parse_args(argv)
    cfg = config_from_args(args)
    # 后面 controller 的逻辑不用动
    controller = AutofocusController(cfg)
    try:
        controller.run()
        return 0
    except AutofocusError as e:
        print(f"\n[错误] {e}")
        return 1
    except KeyboardInterrupt:
        print("\n[中断] 用户中止")
        return 1
    except Exception as e:
        print(f"\n[错误] {e}")
        return 1
    finally:
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
