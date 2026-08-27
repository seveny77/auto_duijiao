# -*- coding: utf-8 -*-
"""E4O4只读连接诊断脚本。

本脚本不会加载参数文件，不会修改编码器位置，不会设置或输出触发，
也不会使能任何比较器。
"""

import argparse
import logging
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion.lct import E4O4Api


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读验证E4O4 DLL、总线、编码器和触发配置",
    )
    parser.add_argument("--dll", required=True)
    parser.add_argument("--slave", type=int, default=1)
    parser.add_argument("--encoder", type=int, default=0)
    parser.add_argument("--trigger", type=int, default=0)
    parser.add_argument("--net-card")
    parser.add_argument(
        "--stage",
        choices=("dll", "bus", "read"),
        required=True,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api = E4O4Api(args.dll)
    print("[SAFE] 本脚本不修改参数、不清零编码器、不输出触发")
    print(f"[STAGE] {args.stage}")
    api.load()
    print(f"[OK] DLL已加载: {api.dll_path}")

    if args.stage == "dll":
        return 0

    try:
        slave_count = api.connect(
            option=0,
            net_card_name=args.net_card,
        )
        print(f"[OK] E4O4总线已连接: slave_count={slave_count}")

        if args.stage == "bus":
            return 0

        print("[INFO] 等待E4O4反馈稳定500ms")
        time.sleep(0.5)

        connect_status = api.get_connect_status(args.slave)
        slave_name = api.get_slave_name(args.slave)
        version = api.get_version(args.slave)
        resource = api.get_slave_resource(args.slave)
        encoder_config = api.get_encoder_config(
            args.slave, args.encoder
        )
        trigger_config = api.get_trigger_config(
            args.slave, args.trigger
        )

        print(f"[INFO] 连接状态原始值: {connect_status}")
        print(f"[INFO] 从站名称: {slave_name}")
        print(
            "[INFO] 版本原始值: "
            f"hardware={version[0]}, module={version[1]}"
        )
        print(f"[INFO] 从站资源: {resource}")
        print(f"[INFO] 编码器配置: {encoder_config}")
        print(f"[INFO] 触发配置: {trigger_config}")
        print(
            "[INFO] 触发脉宽: "
            f"{trigger_config.pulse_width_10ns} x 10ns = "
            f"{trigger_config.pulse_width_us:.2f}us"
        )

        for sample_index in range(5):
            position = api.get_encoder_position(
                args.slave, args.encoder
            )
            print(
                f"[SAMPLE {sample_index + 1}] "
                f"encoder={position}"
            )
            time.sleep(0.2)

        return 0
    finally:
        api.close()
        print("[CLEANUP] E4O4总线已关闭")


if __name__ == "__main__":
    raise SystemExit(main())
