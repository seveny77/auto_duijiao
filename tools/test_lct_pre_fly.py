# -*- coding: utf-8 -*-
"""M60与E4O4预设定比较器联合单点飞拍测试。"""

import argparse
import logging
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion.lct import E4O4Api, M60Api


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="低速短距离验证E4O4预设定比较器单点触发",
    )
    parser.add_argument("--m60-dll", required=True)
    parser.add_argument("--e4o4-dll", required=True)
    parser.add_argument("--eni", required=True)
    parser.add_argument("--axis-param", required=True)
    parser.add_argument("--card", type=int, default=0)
    parser.add_argument("--axis", type=int, default=1)
    parser.add_argument("--slave", type=int, default=1)
    parser.add_argument("--encoder", type=int, default=0)
    parser.add_argument("--trigger", type=int, default=0)
    parser.add_argument("--pre", type=int, default=0)
    parser.add_argument("--delta", type=int, default=10000)
    parser.add_argument("--trigger-offset", type=int, default=5000)
    parser.add_argument("--velocity", type=float, default=10000.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 0 < args.trigger_offset < args.delta:
        raise ValueError("trigger-offset必须位于0和delta之间")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    m60 = M60Api(args.m60_dll)
    e4o4 = E4O4Api(args.e4o4_dll)
    move_started = False

    print("[REAL SINGLE FLY] 将输出1个E4O4硬件触发脉冲")
    m60.load()
    e4o4.load()
    try:
        m60.open(args.card)
        m60.load_eni(args.eni)
        m60.reset_fpga()
        time.sleep(0.5)
        m60.connect_ecat(option=0)
        time.sleep(0.5)
        m60.load_axis_params(args.axis_param)
        time.sleep(1.0)
        e4o4.connect(option=0)
        time.sleep(0.5)
        e4o4.configure_trigger_idle(
            args.slave, args.trigger, 2000, 0
        )

        m60_start = m60.get_actual_position(args.axis)
        e4o4_start = e4o4.get_encoder_position(
            args.slave, args.encoder
        )
        target = m60_start + args.delta
        trigger_position = e4o4_start + args.trigger_offset
        print(
            f"[POSITION] m60={m60_start}, e4o4={e4o4_start}, "
            f"offset={e4o4_start - m60_start}, "
            f"trigger={trigger_position}, target={target}"
        )

        pre_config = e4o4.configure_pre_compare(
            slave_no=args.slave,
            encoder_no=args.encoder,
            precompare_no=args.pre,
            trigger_no=args.trigger,
            positions=[trigger_position],
            direction=0,
            polarity=0,
        )
        print(f"[CONFIG] {pre_config}")

        m60.servo_on(args.axis)
        e4o4.arm_pre_compare(args.slave, args.pre)
        print("[ARMED] 预设定比较器已开启")
        m60.absolute_move(
            args.axis, target, args.velocity
        )
        move_started = True
        m60_actual = m60.wait_motion_complete(
            args.axis, target, args.timeout, 100
        )
        move_started = False
        time.sleep(0.1)

        e4o4_actual = e4o4.get_encoder_position(
            args.slave, args.encoder
        )
        trigger_count = e4o4.get_trigger_count(
            args.slave, args.trigger
        )
        print(
            f"[DELTA] m60={m60_actual - m60_start}, "
            f"e4o4={e4o4_actual - e4o4_start}"
        )
        print(
            f"[TRIGGER] expected={pre_config.expected_trigger_count}, "
            f"actual={trigger_count}"
        )
        if trigger_count != 1:
            raise RuntimeError("E4O4预设定比较器单点触发计数错误")

        print("[OK] E4O4预设定比较器单点飞拍验证通过")
        return 0
    finally:
        if e4o4.is_connected:
            try:
                e4o4.disarm_pre_compare(
                    args.slave, args.pre, args.trigger
                )
            except Exception:
                logging.exception("关闭E4O4预设定比较器失败")
            e4o4.close()
        if m60.ecat_connected:
            if move_started:
                try:
                    m60.stop(args.axis, emergency=True)
                except Exception:
                    logging.exception("异常停止M60失败")
            try:
                m60.servo_off(args.axis)
            except Exception:
                logging.exception("M60去使能失败")
        m60.close()
        print("[CLEANUP] 比较器已关闭，轴已去使能，两套总线已关闭")


if __name__ == "__main__":
    raise SystemExit(main())
