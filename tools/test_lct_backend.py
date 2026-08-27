# -*- coding: utf-8 -*-
"""正式LctMotionBackend的真实小范围验收。"""

import argparse
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion.lct import LctMotionBackend, LctMotionConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m60-dll", required=True)
    parser.add_argument("--e4o4-dll", required=True)
    parser.add_argument("--eni", required=True)
    parser.add_argument("--axis-param", required=True)
    parser.add_argument("--start", type=int, default=3000)
    parser.add_argument("--end", type=int, default=3100)
    parser.add_argument("--step", type=int, default=20)
    parser.add_argument("--capture", type=int, default=3050)
    parser.add_argument(
        "--home",
        action="store_true",
        help="明确授权真实回零；未指定时只连接并读取状态",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = LctMotionConfig(
        m60_dll_path=args.m60_dll,
        e4o4_dll_path=args.e4o4_dll,
        eni_path=args.eni,
        axis_param_path=args.axis_param,
        positioning_velocity_um_s=100.0,
        scan_velocity_um_s=100.0,
        single_capture_approach_um=50,
        single_capture_exit_um=50,
        trigger_pulse_width_10ns=2000,
    )
    backend = LctMotionBackend(config)
    print("[REAL BACKEND TEST] 验收正式运动后端的两个公共动作")
    try:
        backend.connect()
        print(f"[OK] 行程: {backend.read_stroke_range()} um")
        print(f"[STATE] 连接后: {backend.get_state()}")
        if not args.home:
            print(
                "[STOP] 默认不执行真实运动；确认机械区域安全后，"
                "重新运行并增加 --home"
            )
            return 2
        home_state = backend.home(timeout_s=900.0)
        print(f"[OK] 回零完成: {home_state}")
        line_count = backend.linear_fly_scan(
            args.start,
            args.end,
            args.step,
            timeout_s=20.0,
        )
        print(f"[OK] linear_fly_scan触发数: {line_count}")
        capture_count = backend.capture_at_position(
            args.capture,
            timeout_s=20.0,
        )
        print(f"[OK] capture_at_position触发数: {capture_count}")
        if line_count != (args.end - args.start) // args.step:
            raise RuntimeError("正式后端线性飞拍张数错误")
        if capture_count != 1:
            raise RuntimeError("正式后端单点飞拍张数错误")
        hold_state = backend.move_to_position(
            args.capture,
            timeout_s=20.0,
        )
        print(f"[OK] 最终保持位置: {hold_state}")
        print("[OK] LctMotionBackend真实验收通过")
        return 0
    finally:
        backend.disconnect()
        print("[CLEANUP] 正式运动后端已安全断开")


if __name__ == "__main__":
    raise SystemExit(main())
