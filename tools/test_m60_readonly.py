# -*- coding: utf-8 -*-
"""M60分阶段只读诊断脚本。

本脚本不包含伺服使能、回零、点动或位置运动函数。
"""

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
        description="分阶段验证M60 DLL、板卡和EtherCAT连接",
    )
    parser.add_argument("--dll", required=True)
    parser.add_argument("--eni", required=True)
    parser.add_argument("--axis-param", required=True)
    parser.add_argument("--card", type=int, default=0)
    parser.add_argument("--axis", type=int, default=1)
    parser.add_argument(
        "--stage",
        choices=("dll", "card", "ecat"),
        required=True,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api = M60Api(args.dll)

    print("[SAFE] 本脚本不调用使能、回零、点动或运动函数")
    print(f"[STAGE] {args.stage}")

    api.load()
    print(f"[OK] DLL已加载: {api.dll_path}")

    if args.stage == "dll":
        return 0

    try:
        api.open(args.card)
        print(f"[OK] 板卡已打开: card={args.card}")
        print(f"[INFO] M60版本: {api.get_version()}")

        if args.stage == "card":
            return 0

        api.load_eni(args.eni)
        print(f"[OK] ENI已加载: {args.eni}")

        api.reset_fpga()
        print("[OK] FPGA已复位，等待500ms")
        time.sleep(0.5)

        api.connect_ecat(option=0)
        print("[OK] EtherCAT已连接，等待500ms")
        time.sleep(0.5)

        api.load_axis_params(args.axis_param)
        print(f"[OK] 轴参数已加载: {args.axis_param}")

        print("[INFO] 等待PDO和位置反馈稳定1000ms")
        time.sleep(1.0)

        resource = api.get_slave_resource()
        print(f"[INFO] 从站资源: {resource}")

        for sample_index in range(5):
            sampled_encoder = api.get_encoder_position(args.axis)
            sampled_actual = api.get_actual_position(args.axis)
            sampled_status = api.get_axis_status(args.axis)
            print(
                f"[SAMPLE {sample_index + 1}] "
                f"encoder={sampled_encoder}, "
                f"actual={sampled_actual}, "
                f"status=0x{sampled_status.raw:08X}"
            )
            time.sleep(0.2)

        position = api.get_encoder_position(args.axis)

        emergency_stop = api.get_emergency_stop()
        axis_status = api.get_axis_status(args.axis)
        drive_status_word = api.get_drive_status_word(args.axis)
        actual_position = api.get_actual_position(args.axis)
        negative_limit, positive_limit = api.get_soft_limits(args.axis)

        print(f"[INFO] 急停状态: {emergency_stop}")
        print(f"[INFO] M60轴状态: {axis_status}")
        print(
            "[INFO] 驱动器状态字: "
            f"0x{drive_status_word:04X}"
        )
        print(
            "[INFO] 驱动器实际位置: "
            f"{actual_position}"
        )
        print(
            "[INFO] 软件限位: "
            f"negative={negative_limit}, "
            f"positive={positive_limit}"
        )

        return 0

    finally:
        api.close()
        print("[CLEANUP] EtherCAT已断开，M60板卡已关闭")


if __name__ == "__main__":
    raise SystemExit(main())
