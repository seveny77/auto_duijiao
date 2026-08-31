# -*- coding: utf-8 -*-
"""E4O4增量下发状态机离线测试（假DLL，无硬件）。

验证 configure_*_fast 的核心语义（11场景26项，含对抗评审硬化项）：
1. 会话首段（缓存无效）自动走全量路径并建立缓存（精确13/16次调用）；
2. 缓存命中时段间只下发变化量，调用序列被**精确钉死**（line 3次/
   换段4次/pre 6次——性能契约，多发一次往返即失败）；
3. 保留绑定清理只关断不解除掩码；armed_line/armed_pre双标志独立，
   任一侧清理失败只迫使该侧回退全量自愈，不影响另一侧fast；
4. fast路径任何异常（DLL故障/回读不一致）都会回退全量路径；
5. 位置表每次必发且必回读核对（点位是逐段变化量）。

用法：
    python app/test/test_e4o4_incremental.py
"""

import ctypes
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from motion.lct.e4o4_api import E4O4Api


class FakeMiniEcatLib:
    """按MiniEcatLib调用约定记录调用并维护寄存器镜像。"""

    def __init__(self):
        self.calls = []
        self.fail_next = {}  # 函数名 -> 待触发的异常次数
        self.corrupt_next = {}  # 函数名 -> 待触发的回读值篡改次数
        self.regs = {
            "line_enable": [0, 0, 0, 0],
            "line_encoder": -1,
            "line_start": 0,
            "line_end": 0,
            "line_interval": 1,
            "pre_enable": [0, 0, 0, 0],
            "pre_encoder": -1,
            "pre_dir": -1,
            "pre_positions": [],
            "line_mask": 0,
            "pre_mask": 0,
            "polarity": 0,
            "out_mode": -1,
            "trig_mode": -1,
            "pulse_width": -1,
            "counter": 7,
        }

    def _maybe_fail(self, name):
        remaining = self.fail_next.get(name, 0)
        if remaining > 0:
            self.fail_next[name] = remaining - 1
            raise RuntimeError(f"模拟{name}通信故障")

    def _record(self, name):
        self._maybe_fail(name)
        self.calls.append(name)

    @staticmethod
    def _val(ctypes_value):
        return (
            ctypes_value.value
            if hasattr(ctypes_value, "value")
            else int(ctypes_value)
        )

    @staticmethod
    def _write(out_ref, value):
        out_ref._obj.value = value

    # ── 线性比较器 ──
    def Mb_E4O4LineCmp_SetEnable(self, slave, cmp_no, enable):
        self._record("LineCmp.SetEnable")
        self.regs["line_enable"][self._val(cmp_no)] = self._val(enable)

    def Mb_E4O4LineCmp_BingdingEncoder(self, slave, enc, cmp_no):
        self._record("LineCmp.BingdingEncoder")
        self.regs["line_encoder"] = self._val(enc)

    def Mb_E4O4LineCmp_GetBingdingEncoder(self, slave, cmp_no, out):
        self._record("LineCmp.GetBingdingEncoder")
        self._write(out, self.regs["line_encoder"])

    def Mb_E4O4LineCmp_SetTriggerData(self, slave, cmp_no, s, e, i):
        self._record("LineCmp.SetTriggerData")
        self.regs["line_start"] = self._val(s)
        self.regs["line_end"] = self._val(e)
        self.regs["line_interval"] = self._val(i)

    def Mb_E4O4LineCmp_GetTriggerData(self, slave, cmp_no, s, e, i):
        self._record("LineCmp.GetTriggerData")
        remaining = self.corrupt_next.get("LineCmp.GetTriggerData", 0)
        if remaining > 0:
            self.corrupt_next["LineCmp.GetTriggerData"] = remaining - 1
            self._write(s, self.regs["line_start"] + 12345)
            self._write(e, self.regs["line_end"])
            self._write(i, self.regs["line_interval"])
            return
        self._write(s, self.regs["line_start"])
        self._write(e, self.regs["line_end"])
        self._write(i, self.regs["line_interval"])

    # ── 预设定比较器 ──
    def Mb_E4O4PreCmp_SetEnable(self, slave, cmp_no, enable):
        self._record("PreCmp.SetEnable")
        self.regs["pre_enable"][self._val(cmp_no)] = self._val(enable)

    def Mb_E4O4PreCmp_ResetTrigData(self, slave, cmp_no):
        self._record("PreCmp.ResetTrigData")
        self.regs["pre_positions"] = []

    def Mb_E4O4PreCmp_BindingEncoder(self, slave, enc, cmp_no):
        self._record("PreCmp.BindingEncoder")
        self.regs["pre_encoder"] = self._val(enc)

    def Mb_E4O4PreCmp_GetBindingEncoder(self, slave, cmp_no, out):
        self._record("PreCmp.GetBindingEncoder")
        self._write(out, self.regs["pre_encoder"])

    def Mb_E4O4PreCmp_SetTrigDir(self, slave, cmp_no, direction):
        self._record("PreCmp.SetTrigDir")
        self.regs["pre_dir"] = self._val(direction)

    def Mb_E4O4PreCmp_SetTrigData(self, slave, cmp_no, array, count):
        self._record("PreCmp.SetTrigData")
        self.regs["pre_positions"] = [
            int(v) for v in array[: self._val(count)]
        ]

    def Mb_E4O4PreCmp_GetTrigDataCnt(self, slave, cmp_no, out):
        self._record("PreCmp.GetTrigDataCnt")
        self._write(out, len(self.regs["pre_positions"]))

    def Mb_E4O4PreCmp_GetTrigData(self, slave, cmp_no, array):
        self._record("PreCmp.GetTrigData")
        for index, value in enumerate(self.regs["pre_positions"]):
            array[index] = value

    # ── 触发输出 ──
    def Mb_E4O4TrigOut_BandingCompare(
        self, slave, trig, line_mask, pre_mask, polarity
    ):
        self._record("TrigOut.BandingCompare")
        self.regs["line_mask"] = self._val(line_mask)
        self.regs["pre_mask"] = self._val(pre_mask)
        self.regs["polarity"] = self._val(polarity)

    def Mb_E4O4TrigOut_ResetCounter(self, slave, trig):
        self._record("TrigOut.ResetCounter")
        self.regs["counter"] = 0

    def Mb_E4O4TrigOut_GetCounter(self, slave, trig, out):
        self._record("TrigOut.GetCounter")
        self._write(out, self.regs["counter"])

    def Mb_E4O4TrigOut_GetOutMode(self, slave, out):
        self._record("TrigOut.GetOutMode")
        self._write(out, self.regs["out_mode"])

    def Mb_E4O4TrigOut_GetTrigMode(self, slave, trig, out):
        self._record("TrigOut.GetTrigMode")
        self._write(out, self.regs["trig_mode"])

    def Mb_E4O4TrigOut_GetPulseWidth(self, slave, trig, out):
        self._record("TrigOut.GetPulseWidth")
        self._write(out, self.regs["pulse_width"])

    def Mb_E4O4TrigOut_GetBanding(self, slave, trig, lm, pm, pol):
        self._record("TrigOut.GetBanding")
        self._write(lm, self.regs["line_mask"])
        self._write(pm, self.regs["pre_mask"])
        self._write(pol, self.regs["polarity"])

    def Mb_E4O4TrigOut_SetOutMode(self, slave, mode):
        self._record("TrigOut.SetOutMode")
        self.regs["out_mode"] = self._val(mode)

    def Mb_E4O4TrigOut_SetTrigMode(self, slave, trig, mode):
        self._record("TrigOut.SetTrigMode")
        self.regs["trig_mode"] = self._val(mode)

    def Mb_E4O4TrigOut_SetPulseWidth(self, slave, trig, width):
        self._record("TrigOut.SetPulseWidth")
        self.regs["pulse_width"] = self._val(width)


def _patch_fake_returns():
    """Mb_*约定返回0为非错误；给全部假方法统一补返回值。"""

    for name, fn in list(vars(FakeMiniEcatLib).items()):
        if not name.startswith("Mb_"):
            continue

        def wrapped(self, *args, _fn=fn, **kwargs):
            result = _fn(self, *args, **kwargs)
            return 0 if result is None else result

        setattr(FakeMiniEcatLib, name, wrapped)


_patch_fake_returns()


def build_api():
    api = E4O4Api("fake/path/MiniEcatLib.dll")
    api._dll = FakeMiniEcatLib()
    api._connected = True
    api._slave_count = 1
    return api


def calls_since(dll, mark):
    return dll.calls[mark:]


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" | {detail}" if detail else ""))
    return bool(condition)


def main() -> int:
    ok = True
    api = build_api()
    dll = api._dll

    # ── 场景1：会话首段，缓存无效 → 全量路径 + 建缓存 ──
    mark = len(dll.calls)
    config = api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=1000, end_position=2000, interval=100,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "首段fast自动走全量(含disarm+绑定+五元组回读)",
        "LineCmp.SetEnable" in seq
        and "LineCmp.BingdingEncoder" in seq
        and "TrigOut.GetPulseWidth" in seq,
        f"{len(seq)}次调用",
    )
    ok &= check(
        "首段全量精确调用次数",
        len(seq) == 13,
        f"{len(seq)}次调用: {seq}",
    )
    ok &= check(
        "首段返回值与预计触发数一致",
        config.expected_trigger_count == 11,
    )
    ok &= check(
        "缓存已建立(valid且掩码=line)",
        api._cmp_cache.valid
        and api._cmp_cache.bound_line_mask == 1
        and api._cmp_cache.bound_pre_mask == 0
        and not api._cmp_cache.armed_line,
    )

    # ── 场景2：arm→保留绑定清理→第二段line（缓存命中，纯增量） ──
    api.arm_line_compare(0 * 0 + 1, 0)
    ok &= check("arm后缓存armed_line=True", api._cmp_cache.armed_line)
    api.disarm_line_compare_keep_binding(1, 0)
    ok &= check(
        "保留绑定清理后armed_line=False且掩码不变",
        not api._cmp_cache.armed_line
        and api._cmp_cache.bound_line_mask == 1
        and api._cmp_cache.bound_pre_mask == 0,
    )
    mark = len(dll.calls)
    config = api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=3000, end_position=3500, interval=50,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "段间line精确调用序列(3次，钉死性能契约)",
        seq == [
            "LineCmp.SetTriggerData",
            "TrigOut.ResetCounter",
            "LineCmp.GetTriggerData",
        ],
        f"{len(seq)}次调用: {seq}",
    )
    ok &= check(
        "新位置表已生效(寄存器镜像)",
        (dll.regs["line_start"], dll.regs["line_end"]) == (3000, 3500),
    )

    # ── 场景3：切pre（首次，pre_binding为空 → 全量） ──
    mark = len(dll.calls)
    pre_config = api.configure_pre_compare_fast(
        slave_no=1, encoder_no=0, precompare_no=0, trigger_no=0,
        positions=[4242], direction=0,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "首段pre自动走全量(含绑定+方向+五元组回读)",
        "PreCmp.BindingEncoder" in seq and "PreCmp.SetTrigDir" in seq,
        f"{len(seq)}次调用",
    )
    ok &= check(
        "首段pre全量精确调用次数",
        len(seq) == 16,
        f"{len(seq)}次调用: {seq}",
    )
    ok &= check(
        "pre全量后缓存掩码=pre",
        api._cmp_cache.bound_pre_mask == 1
        and api._cmp_cache.bound_line_mask == 0,
    )

    # ── 场景4：下一轮回line（掩码需切回） ──
    api.arm_line_compare(1, 0)
    api.disarm_line_compare_keep_binding(1, 0)
    mark = len(dll.calls)
    api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=5000, end_position=6000, interval=100,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "pre→line换段精确调用序列(4次，增量+一次掩码切换)",
        seq == [
            "TrigOut.BandingCompare",
            "LineCmp.SetTriggerData",
            "TrigOut.ResetCounter",
            "LineCmp.GetTriggerData",
        ],
        f"{len(seq)}次调用: {seq}",
    )
    ok &= check(
        "换段后缓存掩码=line",
        api._cmp_cache.bound_line_mask == 1
        and api._cmp_cache.bound_pre_mask == 0,
    )

    # ── 场景5：第二段pre（缓存命中，纯增量） ──
    api.arm_line_compare(1, 0)
    api.disarm_line_compare_keep_binding(1, 0)
    mark = len(dll.calls)
    pre_config = api.configure_pre_compare_fast(
        slave_no=1, encoder_no=0, precompare_no=0, trigger_no=0,
        positions=[7777], direction=0,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "段间pre精确调用序列(6次，含掩码切回pre——掩码对回归)",
        seq == [
            "PreCmp.ResetTrigData",
            "PreCmp.SetTrigData",
            "TrigOut.BandingCompare",
            "TrigOut.ResetCounter",
            "PreCmp.GetTrigDataCnt",
            "PreCmp.GetTrigData",
        ],
        f"{len(seq)}次调用: {seq}",
    )
    ok &= check(
        "pre新点位已生效",
        dll.regs["pre_positions"] == [7777]
        and pre_config.positions == (7777,),
    )

    # ── 场景6：fast路径异常 → 缓存失效并自动回退全量 ──
    dll.fail_next["LineCmp.SetTriggerData"] = 1
    mark = len(dll.calls)
    config = api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=9000, end_position=9100, interval=100,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "异常自动降级：全量路径兜底成功",
        "LineCmp.BingdingEncoder" in seq
        and config.start_position == 9000,
        f"{len(seq)}次调用",
    )
    ok &= check(
        "降级后缓存重建(valid=True)",
        api._cmp_cache.valid,
    )

    # ── 场景7：armed状态下fast拒绝增量（回退全量，全量自带disarm） ──
    api.arm_line_compare(1, 0)
    mark = len(dll.calls)
    api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=12000, end_position=12100, interval=100,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "armed时fast回退全量(先disarm再配置)",
        "LineCmp.SetEnable" in seq,
        f"{len(seq)}次调用",
    )

    # ── 场景8：完整disarm后掩码清零，下段fast需重新绑定掩码 ──
    api.disarm_line_compare(1, 0, 0, 0)
    ok &= check(
        "完整disarm后掩码=0",
        api._cmp_cache.bound_line_mask == 0
        and api._cmp_cache.bound_pre_mask == 0,
    )
    mark = len(dll.calls)
    api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=15000, end_position=15100, interval=100,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "掩码丢失后fast仍纯增量(补一次BandingCompare)",
        "TrigOut.BandingCompare" in seq
        and "LineCmp.BingdingEncoder" not in seq,
    )

    # ── 场景9：pre fast路径DLL异常 → 缓存失效并回退全量 ──
    dll.fail_next["PreCmp.SetTrigData"] = 1
    mark = len(dll.calls)
    pre_config = api.configure_pre_compare_fast(
        slave_no=1, encoder_no=0, precompare_no=0, trigger_no=0,
        positions=[31337], direction=0,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "pre fast异常自动回退全量(绑定+方向都在)",
        "PreCmp.BindingEncoder" in seq
        and "PreCmp.SetTrigDir" in seq,
        f"{len(seq)}次调用",
    )
    ok &= check(
        "回退后结果正确且缓存重建",
        pre_config.positions == (31337,) and api._cmp_cache.valid,
    )

    # ── 场景10：line回读值被篡改 → 回读不一致异常 → 回退全量 ──
    dll.corrupt_next["LineCmp.GetTriggerData"] = 1
    mark = len(dll.calls)
    config = api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=17000, end_position=17100, interval=100,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "回读不一致自动回退全量",
        "LineCmp.BingdingEncoder" in seq
        and config.start_position == 17000,
        f"{len(seq)}次调用",
    )

    # ── 场景11：pre清理失败 → armed_pre保持True → pre fast回退全量，
    #            且line fast不受影响（armed双标志独立性） ──
    api.arm_pre_compare(1, 0)
    dll.fail_next["PreCmp.SetEnable"] = 1
    disarmed = True
    try:
        api.disarm_pre_compare_keep_binding(1, 0)
    except RuntimeError:
        disarmed = False
    ok &= check(
        "pre清理失败后armed_pre保持True(等待全量自愈)",
        not disarmed and api._cmp_cache.armed_pre,
    )
    mark = len(dll.calls)
    api.configure_pre_compare_fast(
        slave_no=1, encoder_no=0, precompare_no=0, trigger_no=0,
        positions=[2048], direction=0,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "armed_pre时pre fast回退全量(先SetEnable(0)自愈)",
        "PreCmp.SetEnable" in seq
        and seq.index("PreCmp.SetEnable")
        < seq.index("PreCmp.BindingEncoder"),
        f"{len(seq)}次调用",
    )
    mark = len(dll.calls)
    api.configure_line_compare_fast(
        slave_no=1, encoder_no=0, line_compare_no=0, trigger_no=0,
        start_position=18000, end_position=18100, interval=100,
    )
    seq = calls_since(dll, mark)
    ok &= check(
        "pre侧失败不影响line fast(armed标志已拆分)",
        "LineCmp.BingdingEncoder" not in seq,
        f"{len(seq)}次调用: {seq}",
    )

    print("\n" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
