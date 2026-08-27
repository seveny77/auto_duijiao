# -*- coding: utf-8 -*-
"""M60低速、短距离、单方向真实运动验收脚本。"""

import argparse
import logging
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion.lct import M60Api


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M60低速短距离正方向运动验收",
    )
    parser.add_argument("--dll", required=True)
    parser.add_argument("--eni", required=True)
    parser.add_argument("--axis-param", required=True)
    parser.add_argument("--card", type=int, default=0)
    parser.add_argument("--axis", type=int, default=1)
    parser.add_argument("--delta", type=int, default=10000)
    parser.add_argument("--velocity", type=float, default=10000.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--tolerance", type=int, default=100)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.delta <= 0:
        raise ValueError("首次运动验收只允许正方向delta > 0")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    api = M60Api(args.dll)
    move_started = False

    print("[REAL MOTION] 将进行一次低速、短距离、正方向真实运动")
    print(
        f"[PARAM] delta={args.delta} count, "
        f"velocity={args.velocity} count/s"
    )

    api.load()
    try:
        api.open(args.card)
        api.load_eni(args.eni)
        api.reset_fpga()
        time.sleep(0.5)
        api.connect_ecat(option=0)
        time.sleep(0.5)
        api.load_axis_params(args.axis_param)
        time.sleep(1.0)

        before_status = api.get_axis_status(args.axis)
        before_position = api.get_actual_position(args.axis)
        negative_limit, positive_limit = api.get_soft_limits(args.axis)
        target = before_position + args.delta

        print(f"[BEFORE] position={before_position}")
        print(f"[BEFORE] status={before_status}")
        print(
            f"[LIMIT] negative={negative_limit}, "
            f"positive={positive_limit}, target={target}"
        )

        if api.get_emergency_stop():
            raise RuntimeError("急停已触发，拒绝真实运动")
        if not negative_limit <= target <= positive_limit:
            raise RuntimeError("目标位置超出软件限位，拒绝真实运动")

        api.servo_on(args.axis)
        print("[OK] 伺服使能状态已确认")

        api.absolute_move(
            axis_no=args.axis,
            target_counts=target,
            velocity_counts_s=args.velocity,
        )
        move_started = True
        print("[OK] 运动命令已发出")

        actual = api.wait_motion_complete(
            axis_no=args.axis,
            target_counts=target,
            timeout_s=args.timeout,
            tolerance_counts=args.tolerance,
        )
        move_started = False
        print(
            f"[OK] 运动完成: target={target}, "
            f"actual={actual}, error={actual - target}"
        )
        return 0
    finally:
        if api.ecat_connected:
            if move_started:
                try:
                    api.stop(args.axis, emergency=True)
                except Exception:
                    logging.exception("异常停止M60失败")
            try:
                api.servo_off(args.axis)
            except Exception:
                logging.exception("M60去使能失败")
        api.close()
        print("[CLEANUP] 轴已去使能，EtherCAT已断开，板卡已关闭")


if __name__ == "__main__":
    raise SystemExit(main())
