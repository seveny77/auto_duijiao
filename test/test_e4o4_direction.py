# -*- coding: utf-8 -*-
"""E4O4比较器方向语义台架测试（真实运动，不接相机）。

一次上机回答五个问题（优化⑥「反向精扫」的前提）：
  A 对照  LineCmp 正向窗口（start<end）上穿 → 期望 7 触发（链路自证）；
  B 关键  LineCmp 反向窗口（start>end）下穿 → 两种结局都是结论：
          7 触发=反向窗口成立；DLL 拒绝（首测实测 -1120）=LineCmp 参数
          级强制 start<end，反向触发不能靠交换起终点实现；
  C 语义  LineCmp 正向窗口被下穿 → 期望 0 触发（窗口是单向触发还是
          双向触发：若非0则是重要发现——正向窗口本身即可服务反向扫）；
  D 官方  PreCmp direction=2（双向）同一配置上穿+下穿 → 各期望 1 触发；
  E 官方  PreCmp direction=1（反向专用档）上穿期望 0、下穿期望 1
          （若成立，反向精扫可走 PreCmp 位置表 + dir=1/2 路线）。

窗口固定 [8500, 8560]µm 步距 10µm（期望 60/10+1=7 个触发点），全部
运动都在已知工作区 8470..8590µm 内、以生产定位速度执行；offset 取会话
内一次静止对读（已证为会话常数）。单项异常只记录不中断整场。

用法（真实运动，需现场确认）：
    python app/test/test_e4o4_direction.py --home          # 交互确认
    python app/test/test_e4o4_direction.py --home --yes    # 跳过确认
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion.lct import LctMotionBackend, LctMotionConfig

WINDOW_LO_UM = 8500.0
WINDOW_HI_UM = 8560.0
STEP_UM = 10.0
EXPECTED_COUNT = int((WINDOW_HI_UM - WINDOW_LO_UM) / STEP_UM) + 1
APPROACH_UM = 30.0
PRE_POS_UM = 8530.0
MOVE_TIMEOUT_S = 10.0
FIRST_MOVE_TIMEOUT_S = 60.0

LOW_PARK_UM = int(WINDOW_LO_UM - APPROACH_UM)
HIGH_PARK_UM = int(WINDOW_HI_UM + APPROACH_UM)


def read_counter_settled(backend, cfg, timeout_s: float = 1.5) -> int:
    """读到计数稳定（连续两次相等）为止，兜住~50ms计数传播延迟。"""

    slave = cfg.e4o4_slave_no
    trig = cfg.trigger_out_no
    last = -1
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        value = backend._e4o4.get_trigger_count(slave, trig)
        if value == last:
            return value
        last = value
        time.sleep(0.1)
    return last


def run_line_test(backend, cfg, offset, window_reversed, motion_up,
                  label, note, expected):
    """执行一个LineCmp方向组合并返回结果字典（异常由调用方兜底）。"""

    slave = cfg.e4o4_slave_no
    enc = cfg.encoder_no
    cmp_no = cfg.line_compare_no
    trig = cfg.trigger_out_no
    lo = cfg.um_to_counts(WINDOW_LO_UM) + offset
    hi = cfg.um_to_counts(WINDOW_HI_UM) + offset
    step = cfg.um_to_counts(STEP_UM)

    park_from = LOW_PARK_UM if motion_up else HIGH_PARK_UM
    park_to = HIGH_PARK_UM if motion_up else LOW_PARK_UM
    backend.move_to_position(park_from, MOVE_TIMEOUT_S)

    start_pos, end_pos = (hi, lo) if window_reversed else (lo, hi)
    config = backend._e4o4.configure_line_compare(
        slave_no=slave,
        encoder_no=enc,
        line_compare_no=cmp_no,
        trigger_no=trig,
        start_position=start_pos,
        end_position=end_pos,
        interval=step,
        polarity=cfg.trigger_polarity,
    )
    backend._e4o4.arm_line_compare(slave, cmp_no)
    t0 = time.perf_counter()
    backend.move_to_position(park_to, MOVE_TIMEOUT_S)
    move_ms = (time.perf_counter() - t0) * 1000
    count = read_counter_settled(backend, cfg)
    e4o4_final = backend._e4o4.get_encoder_position(slave, enc)
    backend._e4o4.disarm_line_compare(
        slave, cmp_no, trig, cfg.trigger_polarity
    )

    ok = count == expected
    print(
        "[DIR-TEST] {label} {note}: 窗口={sw}..{ew}(E4O4计数) "
        "运动={f}→{t}µm {ms:.0f}ms | 触发 {got} / 期望 {exp} "
        "→ {verdict} | e4o4_final={fin}".format(
            label=label,
            note=note,
            sw=start_pos,
            ew=end_pos,
            f=park_from,
            t=park_to,
            ms=move_ms,
            got=count,
            exp=expected,
            verdict="PASS" if ok else "**不符**",
            fin=e4o4_final,
        )
    )
    return {
        "ok": ok,
        "count": count,
        "expected": expected,
        "window_start": start_pos,
        "window_end": end_pos,
        "motion_from_um": park_from,
        "motion_to_um": park_to,
        "e4o4_final": e4o4_final,
        "configured_count": config.expected_trigger_count,
    }


def run_pre_direction_test(backend, cfg, offset, direction,
                           expect_up, expect_down, label):
    """PreCmp指定方向档：同一配置先上穿后下穿，分别读数。"""

    slave = cfg.e4o4_slave_no
    enc = cfg.encoder_no
    pre_no = cfg.precompare_no
    trig = cfg.trigger_out_no
    position = cfg.um_to_counts(PRE_POS_UM) + offset

    backend.move_to_position(LOW_PARK_UM, MOVE_TIMEOUT_S)
    backend._e4o4.configure_pre_compare(
        slave_no=slave,
        encoder_no=enc,
        precompare_no=pre_no,
        trigger_no=trig,
        positions=[position],
        direction=direction,
        polarity=cfg.trigger_polarity,
    )
    backend._e4o4.arm_pre_compare(slave, pre_no)

    backend.move_to_position(HIGH_PARK_UM, MOVE_TIMEOUT_S)
    up_count = read_counter_settled(backend, cfg)

    backend._e4o4.reset_trigger_count(slave, trig)
    backend.move_to_position(LOW_PARK_UM, MOVE_TIMEOUT_S)
    down_count = read_counter_settled(backend, cfg)
    backend._e4o4.disarm_pre_compare(
        slave, pre_no, trig, cfg.trigger_polarity
    )

    ok = up_count == expect_up and down_count == expect_down
    print(
        "[DIR-TEST] {label} PreCmp(dir={d}) 点位={p}（E4O4计数）: "
        "上穿 {up}/期望{eu} + 下穿 {down}/期望{ed} → {verdict}".format(
            label=label,
            d=direction,
            p=position,
            up=up_count,
            eu=expect_up,
            down=down_count,
            ed=expect_down,
            verdict="PASS" if ok else "**不符**",
        )
    )
    return {
        "ok": ok,
        "up_count": up_count,
        "down_count": down_count,
        "expect_up": expect_up,
        "expect_down": expect_down,
        "position": position,
        "direction": direction,
    }


def safe_call(results, key, fn, *args, **kwargs):
    """单项异常只记录不中断整场（B 预期可能被 DLL 拒绝）。"""

    try:
        results[key] = fn(*args, **kwargs)
    except Exception as exc:
        print(
            "[DIR-TEST] {key} 异常（记录，继续后续项）: "
            "{kind}: {msg}".format(key=key, kind=type(exc).__name__, msg=exc)
        )
        results[key] = {
            "ok": False,
            "error": "{}: {}".format(type(exc).__name__, exc),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="E4O4比较器方向语义台架测试（真实运动）",
    )
    parser.add_argument(
        "--config",
        default="gui/config.json",
        help="GUI配置文件（含motion节）",
    )
    parser.add_argument(
        "--home",
        action="store_true",
        help="明确授权本脚本执行真实回零与飞拍运动",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过现场交互确认",
    )
    parser.add_argument(
        "--output-dir",
        default="output/e4o4_direction",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with config_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    motion_values = data.get("motion")
    if not isinstance(motion_values, dict):
        print(f"[STOP] 配置缺少motion对象: {config_path}")
        return 2
    cfg = LctMotionConfig(**motion_values)

    print(
        "[DIR-TEST] 窗口={}..{}µm 步距{}µm 期望{}触发 | "
        "运动范围 {}..{}µm".format(
            WINDOW_LO_UM, WINDOW_HI_UM, STEP_UM, EXPECTED_COUNT,
            LOW_PARK_UM, HIGH_PARK_UM,
        )
    )
    print("[REAL] 本脚本将执行回零与真实轴运动（不接相机，仅数触发）")
    if not args.home:
        print("[STOP] 未提供--home，本脚本不会连接或产生真实运动")
        return 2
    if not args.yes:
        answer = input(
            "确认机械区域安全、急停可用，并允许回零和真实运动。"
            "请输入 RUN 继续: "
        )
        if answer.strip().upper() != "RUN":
            print("[CANCELLED] 用户未确认，未连接硬件")
            return 2

    backend = LctMotionBackend(cfg)
    backend.connect()
    results = {}
    offset = None
    try:
        backend.home(timeout_s=cfg.home_timeout_s)
        backend.move_to_position(LOW_PARK_UM, FIRST_MOVE_TIMEOUT_S)
        m60, e4o4 = backend._sample_coordinate_pair()
        offset = e4o4 - m60
        print(
            "[DIR-TEST] 会话offset = e4o4({}) - m60({}) = {}".format(
                e4o4, m60, offset
            )
        )

        safe_call(
            results, "A_正向窗上穿", run_line_test,
            backend, cfg, offset,
            window_reversed=False, motion_up=True,
            label="A", note="正向窗对照", expected=EXPECTED_COUNT,
        )
        safe_call(
            results, "B_反向窗下穿", run_line_test,
            backend, cfg, offset,
            window_reversed=True, motion_up=False,
            label="B", note="反向窗关键项", expected=EXPECTED_COUNT,
        )
        safe_call(
            results, "C_正向窗下穿", run_line_test,
            backend, cfg, offset,
            window_reversed=False, motion_up=False,
            label="C", note="单向语义验证", expected=0,
        )
        safe_call(
            results, "D_PreCmp双向", run_pre_direction_test,
            backend, cfg, offset, direction=2,
            expect_up=1, expect_down=1, label="D",
        )
        safe_call(
            results, "E_PreCmp反向档", run_pre_direction_test,
            backend, cfg, offset, direction=1,
            expect_up=0, expect_down=1, label="E",
        )
    finally:
        backend.disconnect()

    print("\n[DIR-TEST] ════════ 结论 ════════")
    result_b = results["B_反向窗下穿"]
    if result_b.get("count") == EXPECTED_COUNT:
        print(
            "[DIR-TEST] ✅ B 通过：LineCmp start>end 反向触发成立，"
            "⑥反向精扫前提满足"
        )
    elif "error" in result_b:
        print(
            "[DIR-TEST] ❌ B 被DLL拒绝（{}）：LineCmp 参数级强制 "
            "start<end，反向触发不能靠交换起终点实现".format(
                result_b["error"]
            )
        )
    else:
        print(
            "[DIR-TEST] ⚠️ B 配置成功但触发不符（{}/{}）：反向窗口"
            "语义存疑".format(result_b.get("count"), EXPECTED_COUNT)
        )
    if results["C_正向窗下穿"].get("count") == 0:
        print(
            "[DIR-TEST] ✅ C 通过：正向窗被反穿零触发，LineCmp为单向"
            "触发语义（生产回穿安全，反向扫不能复用正向窗）"
        )
    else:
        print(
            "[DIR-TEST] ⚠️ C 不符：正向窗被反穿出现 {} 触发，LineCmp"
            "为双向触发——正向窗本身即可服务反向扫".format(
                results["C_正向窗下穿"].get("count")
            )
        )
    if results["D_PreCmp双向"].get("ok"):
        print("[DIR-TEST] ✅ D 通过：PreCmp direction=2 双向触发可用")
    else:
        print("[DIR-TEST] ❌ D 不符：PreCmp 双向模式与手册描述不一致")
    if results["E_PreCmp反向档"].get("ok"):
        print(
            "[DIR-TEST] ✅ E 通过：PreCmp direction=1 反向专用档成立，"
            "⑥反向精扫可走 PreCmp位置表+方向档 路线"
        )
    else:
        print("[DIR-TEST] ❌ E 不符：PreCmp 反向档与手册描述不一致")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{stamp}.json"
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "window_um": [WINDOW_LO_UM, WINDOW_HI_UM],
                "step_um": STEP_UM,
                "expected_count": EXPECTED_COUNT,
                "offset": offset,
                "results": results,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )
    print(f"[OUTPUT] {output_path}")
    all_ok = all(
        item.get("ok") or "error" in item for item in results.values()
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
