# -*- coding: utf-8 -*-
"""配置E4O4编码器与触发口的静态安全参数。"""

import argparse
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion.lct import E4O4Api


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="设置E4O4静态参数并回读，不输出触发脉冲",
    )
    parser.add_argument("--dll", required=True)
    parser.add_argument("--slave", type=int, default=1)
    parser.add_argument("--encoder", type=int, default=0)
    parser.add_argument("--trigger", type=int, default=0)
    parser.add_argument("--multiplier", type=int, default=4)
    parser.add_argument("--direction", type=int, default=1)
    parser.add_argument("--pulse-width-10ns", type=int, default=2000)
    parser.add_argument("--polarity", type=int, default=0)
    parser.add_argument("--net-card")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    api = E4O4Api(args.dll)

    print("[SAFE] 先解除比较器绑定；不移动轴、不输出触发、不修改位置")
    api.load()
    try:
        api.connect(option=0, net_card_name=args.net_card)

        before_encoder = api.get_encoder_config(
            args.slave, args.encoder
        )
        before_trigger = api.get_trigger_config(
            args.slave, args.trigger
        )
        print(f"[BEFORE] 编码器配置: {before_encoder}")
        print(f"[BEFORE] 触发配置: {before_trigger}")

        after_encoder = api.configure_encoder(
            slave_no=args.slave,
            encoder_no=args.encoder,
            multiplier=args.multiplier,
            direction=args.direction,
            enabled=True,
        )
        after_trigger = api.configure_trigger_idle(
            slave_no=args.slave,
            trigger_no=args.trigger,
            pulse_width_10ns=args.pulse_width_10ns,
            polarity=args.polarity,
        )

        print(f"[AFTER] 编码器配置: {after_encoder}")
        print(f"[AFTER] 触发配置: {after_trigger}")

        expected_encoder = (
            args.multiplier,
            args.direction,
            True,
        )
        actual_encoder = (
            after_encoder.multiplier,
            after_encoder.direction,
            after_encoder.enabled,
        )
        if actual_encoder != expected_encoder:
            raise RuntimeError(
                "E4O4编码器配置回读不一致: "
                f"expected={expected_encoder}, actual={actual_encoder}"
            )
        if (
            after_trigger.output_mode != 1
            or after_trigger.trigger_mode != 0
            or after_trigger.pulse_width_10ns
            != args.pulse_width_10ns
            or after_trigger.line_compare_mask != 0
            or after_trigger.precompare_mask != 0
            or after_trigger.polarity != args.polarity
        ):
            raise RuntimeError(
                "E4O4触发静态配置回读不一致: "
                f"actual={after_trigger}"
            )

        print(
            "[OK] 静态配置已回读确认: "
            f"pulse_width={after_trigger.pulse_width_us:.2f}us, "
            "compare_masks=0"
        )
        return 0
    finally:
        api.close()
        print("[CLEANUP] E4O4总线已关闭")


if __name__ == "__main__":
    raise SystemExit(main())
