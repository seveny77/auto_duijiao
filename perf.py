# -*- coding: utf-8 -*-
"""CT（Cycle Time）采集注册表与统一计时日志。

全系统唯一的耗时采集入口，三个能力：

1. record()/stage()：把一个环节的墙钟时间记入注册表，并立刻打一条
   ``[CT]`` 前缀的 INFO 日志——日志里每个环节的执行时间直接可见；
2. instrument_class()：给硬件封装类（M60Api/E4O4Api/HikCamera 等）的
   全部公开方法套一层计时壳，每次 DLL/SDK 调用都进注册表。默认不打
   日志（轮询类调用太频繁），单次超过 slow_ms 才以 ``[CT][慢]`` 升
   INFO 提示；
3. snapshot()/reset()/stats()/log_summary()：注册表快照、清零、聚合
   统计与汇总日志。测试脚本（app/test/test_search_ct.py）靠
   reset()/snapshot() 拿到逐环节 CT 数据。

命名约定（注册表 key）：
- 搜索级：沿用 pipeline ct 字典的既有键名（coarse_motion_ms 等）；
- 飞拍内部：``lct.<phase>.<key>``（detail_ct 各键 + 新拆分的键）；
- 阶段级：``phase.<coarse|fine|calibrate>.<key>``（run_phase 各键）；
- 硬件调用：``hw.m60.<method>`` / ``hw.e4o4.<method>`` / ``hw.cam.<method>``。

日志约定：所有 CT 行以 ``[CT]`` 开头，grep "\\[CT\\]" 即得全链路耗时。
"""

import functools
import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Dict, Iterable

logger = logging.getLogger("perf")

# 每个环节最多保留的样本数。轮询类硬件调用（如 20ms 一次的状态读取）
# 样本极多，封顶防止长时间运行的 GUI 进程内存缓慢增长。
_MAX_SAMPLES = 1024

_lock = threading.Lock()
_records: Dict[str, deque] = {}

# key -> 中文标签。record/instrument 打日志时优先用标签，汇总表更可读。
LABELS = {
    # 搜索级（pipeline ct 字典）
    "template_load_ms": "模板加载",
    "camera_setup_ms": "相机打开与粗扫参数下发",
    "predict_ms": "粗扫+NCC预测(策略)",
    "coarse_total_ms": "粗扫总耗时",
    "coarse_collector_start_ms": "粗扫·取流启动",
    "coarse_motion_ms": "粗扫·飞拍运动",
    "coarse_frame_wait_ms": "粗扫·等帧评价",
    "coarse_stabilize_ms": "粗扫·稳定期",
    "coarse_stop_ms": "粗扫·停流",
    "ncc_ms": "NCC互相关计算",
    "yolo_ms": "YOLO ROI检测",
    "fine_switch_ms": "精扫·相机切换",
    "fine_ms": "精扫总耗时",
    "fine_collector_start_ms": "精扫·取流启动",
    "fine_motion_ms": "精扫·飞拍运动",
    "fine_frame_wait_ms": "精扫·等帧评价",
    "fine_stabilize_ms": "精扫·稳定期",
    "fine_stop_ms": "精扫·停流",
    "final_switch_ms": "单点取证·相机切换",
    "final_collector_start_ms": "单点取证·取流启动",
    "single_capture_ms": "单点飞拍(含定位运动)",
    "final_frame_wait_ms": "单点取证·等帧",
    "final_stop_ms": "单点取证·停流",
    "final_hold_ms": "回最终位并伺服保持",
    "final_ms": "单点取证+回位总耗时",
    "total_ms": "搜索总耗时",
    "cleanup_ms": "结束清理(停流+关相机)",
    "total_with_cleanup_ms": "总耗时(含清理)",
    # 标定级
    "cal_camera_setup_ms": "标定·相机准备",
    "cal_flyscan_ms": "标定·飞拍",
    "cal_collector_start_ms": "标定·取流启动",
    "cal_motion_ms": "标定·飞拍运动",
    "cal_frame_wait_ms": "标定·等帧评价",
    "cal_stabilize_ms": "标定·稳定期",
    "cal_stop_ms": "标定·停流",
    "template_gen_ms": "标定·模板生成",
    # run_phase 阶段键（phase.<name>.<key> 中的 key 部分）
    "collector_start_ms": "取流启动",
    "motion_ms": "飞拍运动",
    "frame_wait_ms": "等帧评价",
    "stabilize_ms": "稳定期",
    "phase_total_ms": "阶段总耗时",
    # 帧级（collector 提供，经 run_phase 记入）
    "first_frame_ms": "首帧到达(自取流启动)",
    "last_frame_ms": "末帧到达(自取流启动)",
    "eval_total_ms": "评价线程总耗时",
    "eval_avg_ms": "单帧平均评价",
    # LCT detail_ct 键（lct.<phase>.<key> 中的 key 部分）
    "positioning_ms": "起点定位运动",
    "coordinate_ms": "坐标采样(m60+e4o4)",
    "compare_config_ms": "比较器配置",
    "compare_arm_ms": "比较器使能",
    "scan_move_issue_ms": "扫描运动·发令",
    "scan_wait_ms": "扫描运动·等待到位",
    "scan_motion_ms": "扫描运动·总计",
    "trigger_settle_ms": "触发计数稳定等待(条件轮询,上限200ms)",
    "trigger_verify_ms": "触发校验(等待+读数)",
    "trigger_reads_ms": "触发/编码器读数",
    "precheck_ms": "就绪预检(回零+静止+伺服)",
    "capture_motion_ms": "单点飞拍·运动",
    "cleanup_lct_ms": "比较器解除与清理",
    "m60_init_ms": "连接·M60初始化(含固定sleep)",
    "e4o4_init_ms": "连接·E4O4初始化(含固定sleep)",
    "status_check_ms": "连接·状态与行程检查",
    # 硬件方法级（hw.<dev>.<method> / lct.<method> 的尾段）
    "linear_fly_scan": "线性飞拍(总)",
    "capture_at_position": "单点飞拍(总)",
    "move_to_position": "定位运动(总)",
    "cancel_current_motion": "运动取消",
    "home": "回零(总)",
    "prepare_new_task": "新任务准备",
    "clear_alarm": "报警复位",
    "servo_on": "M60伺服使能",
    "servo_off": "M60伺服去使能",
    "get_axis_status": "M60状态读取",
    "get_actual_position": "M60位置读取",
    "get_emergency_stop": "M60急停读取",
    "wait_motion_complete": "M60等待到位(轮询)",
    "absolute_move": "M60绝对运动发令",
    "get_trigger_count": "E4O4触发计数读取",
    "get_encoder_position": "E4O4编码器读取",
    "configure_line_compare": "E4O4线性比较器配置",
    "configure_pre_compare": "E4O4预比较器配置",
    "configure_line_compare_fast": "E4O4线性比较器增量配置",
    "configure_pre_compare_fast": "E4O4预比较器增量配置",
    "disarm_line_compare_keep_binding": "E4O4线性比较器关断(保留绑定)",
    "disarm_pre_compare_keep_binding": "E4O4预比较器关断(保留绑定)",
    "arm_line_compare": "E4O4比较器使能",
    "set_exposure": "相机曝光设置",
    "set_gain": "相机增益设置",
    "set_roi": "相机ROI设置",
    "set_binning": "相机binning设置",
    "set_decimation": "相机decimation设置",
    "set_trigger_mode": "相机触发模式设置",
    "start_grabbing": "相机开流",
    "stop_grabbing": "相机停流",
    "register_frame_callback": "相机回调注册",
    "trigger_software": "相机软触发",
    "capture_frame": "相机单帧采集",
    "get_sensor_size": "相机传感器尺寸读取",
    # 完整键：防止 open/close 等通用名尾段误配到错误设备
    "hw.cam.open": "相机打开(枚举+句柄)",
    "hw.cam.close": "相机关闭",
    "hw.m60.open": "M60打开",
    "hw.m60.close": "M60关闭",
    "hw.m60.load": "M60 DLL加载",
    "hw.e4o4.connect": "E4O4总线连接",
    "hw.e4o4.close": "E4O4关闭",
    "lct.connect": "运动后端连接(总)",
    "lct.get_state": "运动后端状态快照",
}


def _label_of(name: str) -> str:
    """优先整体匹配；否则用最后一段匹配（phase.xxx./lct.xxx. 前缀键）。"""
    if name in LABELS:
        return LABELS[name]
    tail = name.rsplit(".", 1)[-1]
    if tail in LABELS:
        return LABELS[tail]
    return name


def record(name: str, ms: float, *, log: bool = True) -> float:
    """记录一个环节的耗时（毫秒）并可选打日志；返回原值方便链式赋值。

    典型用法（pipeline）：
        ct["camera_setup_ms"] = perf.record(
            "camera_setup_ms", (time.perf_counter() - t0) * 1000)
    """
    with _lock:
        bucket = _records.get(name)
        if bucket is None:
            bucket = deque(maxlen=_MAX_SAMPLES)
            _records[name] = bucket
        bucket.append(float(ms))
    if log:
        logger.info("[CT] %s %.1fms", _label_of(name), ms)
    return ms


def ingest(mapping: Dict[str, float], prefix: str = "") -> None:
    """批量记录外部已算好的耗时表（不打逐条日志，静默入表）。"""
    for key, value in dict(mapping).items():
        name = f"{prefix}.{key}" if prefix else key
        record(name, float(value), log=False)


@contextmanager
def stage(name: str, *, log: bool = True):
    """上下文管理器：测量一段代码的墙钟时间。

    with perf.stage("template_load_ms"):
        template = FocusTemplate.load(cfg.template)
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, (time.perf_counter() - t0) * 1000, log=log)


def snapshot() -> Dict[str, list]:
    """返回注册表只读快照 {name: [ms, ...]}。"""
    with _lock:
        return {name: list(bucket) for name, bucket in _records.items()}


def reset() -> None:
    """清空注册表。测试脚本每轮 run_search 前调用，实现逐轮隔离。"""
    with _lock:
        _records.clear()


def stats() -> Dict[str, Dict[str, float]]:
    """聚合统计 {name: {n, total, mean, min, max}}。"""
    result = {}
    for name, samples in snapshot().items():
        if not samples:
            continue
        result[name] = {
            "n": len(samples),
            "total": sum(samples),
            "mean": sum(samples) / len(samples),
            "min": min(samples),
            "max": max(samples),
        }
    return result


def log_summary(title: str, total_key: str = "total_ms") -> None:
    """输出注册表汇总：按总耗时降序，一行一个环节。

    百分比基准取 total_key 的均值（通常是 search 的 total_ms）。
    """
    st = stats()
    if not st:
        logger.info("[CT] %s：无数据", title)
        return
    total = st.get(total_key, {}).get("mean")
    lines = ["[CT] " + "═" * 12 + " %s CT 汇总 " % title + "═" * 12]
    ordered = sorted(st.items(), key=lambda kv: -kv[1]["total"])
    for name, s in ordered:
        pct = ""
        if total:
            pct = " %5.1f%%" % (s["mean"] / total * 100)
        lines.append(
            "[CT] %-44s 总%9.1fms 均%9.1fms 最大%9.1fms n=%-3d%s"
            % (_label_of(name), s["total"], s["mean"], s["max"], s["n"], pct)
        )
    for line in lines:
        logger.info("%s", line)


def _timed_method(func, name: str, slow_ms: float):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            record(name, ms, log=False)
            if ms >= slow_ms:
                logger.info("[CT][慢] %s %.1fms", _label_of(name), ms)

    return wrapper


def instrument_class(cls, prefix: str, *, slow_ms: float = 100.0,
                     exclude: Iterable[str] = ()) -> None:
    """给类的全部公开普通方法套计时壳（原地替换，返回值不变）。

    只处理普通函数（跳过 property/staticmethod/classmethod 和下划线
    私有成员），因此生命周期、参数下发、状态读取每一次 SDK/DLL 往返
    都有 CT 记录，但不会影响帧回调热路径（回调不是方法调用）。
    """
    excluded = set(exclude)
    for attr, obj in list(vars(cls).items()):
        if attr.startswith("_") or attr in excluded:
            continue
        if not isinstance(obj, type(lambda: 0)):
            continue
        setattr(cls, attr, _timed_method(obj, f"{prefix}.{attr}", slow_ms))
