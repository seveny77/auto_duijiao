# -*- coding: utf-8 -*-
"""M60与E4O4线性比较器联合短距离飞拍计数测试。"""

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
        description="低速短距离验证M60运动与E4O4线性比较触发计数",
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
    parser.add_argument("--line", type=int, default=0)
    parser.add_argument("--delta", type=int, default=12000)
    parser.add_argument("--velocity", type=float, default=10000.0)
    parser.add_argument("--interval", type=int, default=2000)
    parser.add_argument("--edge-margin", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--keep-servo-on",
        action="store_true",
        help="成功后保持终点和伺服使能，按Enter后才执行清理",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.delta <= 0:
        raise ValueError("首次联合测试只允许正方向delta > 0")
    if args.edge_margin <= 0 or args.delta <= 2 * args.edge_margin:
        raise ValueError("delta必须大于两倍edge-margin")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    m60 = M60Api(args.m60_dll)
    e4o4 = E4O4Api(args.e4o4_dll)
    move_started = False

    print("[REAL FLY TEST] 将产生少量E4O4硬件触发脉冲")
    print(
        f"[PARAM] delta={args.delta}, velocity={args.velocity}, "
        f"interval={args.interval}, edge_margin={args.edge_margin}"
    )

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
            slave_no=args.slave,
            trigger_no=args.trigger,
            pulse_width_10ns=2000,
            polarity=0,
        )

        m60_start = m60.get_actual_position(args.axis)
        e4o4_start = e4o4.get_encoder_position(
            args.slave, args.encoder
        )
        target = m60_start + args.delta
        trigger_start = e4o4_start + args.edge_margin
        trigger_end = e4o4_start + args.delta - args.edge_margin
        coordinate_offset = e4o4_start - m60_start

        print(
            f"[POSITION] m60={m60_start}, e4o4={e4o4_start}, "
            f"offset={coordinate_offset}"
        )

        line_config = e4o4.configure_line_compare(
            slave_no=args.slave,
            encoder_no=args.encoder,
            line_compare_no=args.line,
            trigger_no=args.trigger,
            start_position=trigger_start,
            end_position=trigger_end,
            interval=args.interval,
            polarity=0,
        )
        print(f"[CONFIG] {line_config}")

        m60.servo_on(args.axis)
        e4o4.arm_line_compare(args.slave, args.line)
        print("[ARMED] 线性比较器已开启")

        m60.absolute_move(
            axis_no=args.axis,
            target_counts=target,
            velocity_counts_s=args.velocity,
        )
        move_started = True
        m60_actual = m60.wait_motion_complete(
            axis_no=args.axis,
            target_counts=target,
            timeout_s=args.timeout,
            tolerance_counts=100,
        )
        move_started = False
        time.sleep(0.1)

        e4o4_actual = e4o4.get_encoder_position(
            args.slave, args.encoder
        )
        trigger_count = e4o4.get_trigger_count(
            args.slave, args.trigger
        )
        m60_delta = m60_actual - m60_start
        e4o4_delta = e4o4_actual - e4o4_start

        print(
            f"[DELTA] m60={m60_delta}, e4o4={e4o4_delta}, "
            f"difference={e4o4_delta - m60_delta}"
        )
        print(
            f"[TRIGGER] expected={line_config.expected_trigger_count}, "
            f"actual={trigger_count}"
        )

        if abs(e4o4_delta - m60_delta) > 100:
            raise RuntimeError("M60与E4O4位置增量差超过100 count")
        if trigger_count != line_config.expected_trigger_count:
            raise RuntimeError("E4O4线性比较触发计数不符合预期")

        print(f"[DONE] 线性飞拍完成，轴保持在终点: {m60_actual} count")
        if args.keep_servo_on:
            input(
                "[HOLD] 伺服仍保持使能；确认机械区域安全后，"
                "按Enter执行去使能和清理: "
            )
        else:
            print("[SAFE] 默认模式将在脚本结束时去使能，不保持伺服")

        print("[OK] M60与E4O4联合线性飞拍计数验证通过")
        return 0
    finally:
        if e4o4.is_connected:
            try:
                e4o4.disarm_line_compare(
                    args.slave, args.line, args.trigger
                )
            except Exception:
                logging.exception("关闭E4O4线性比较器失败")
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
