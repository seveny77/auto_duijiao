# -*- coding: utf-8 -*-
"""VA 标定：起点不触发、终点触发，按帧顺序保存位置（um）和清晰度。

只使用 Python 标准库；接收普通参数并返回 JSON 字符串，不读写软件变量。
位置按采样顺序推算，是指令位置，不是编码器实测位置。
"""

import json
import math
from decimal import Decimal, InvalidOperation


def _finite_score(value):
    if isinstance(value, bool):
        raise ValueError("清晰度不能是布尔值")
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("清晰度必须是有效数值")
    if not math.isfinite(score):
        raise ValueError("清晰度不能是 NaN 或无穷大")
    return score


def _decimal(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(name + "必须是有效数值")
    if not result.is_finite():
        raise ValueError(name + "不能是 NaN 或无穷大")
    return result


def _json_number(value):
    if value == value.to_integral_value():
        return int(value)
    result = float(value)
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise ValueError("位置或步距超出 JSON 浮点数可表示范围")
    return result


def _scan_parameters(start_position, end_position, step):
    start = _decimal(start_position, "起点")
    end = _decimal(end_position, "终点")
    spacing = _decimal(step, "步距")
    if spacing <= 0:
        raise ValueError("步距必须大于 0；运动方向由起点和终点自动确定")
    if start == end:
        raise ValueError("起点和终点不能相同")
    steps = abs(end - start) / spacing
    if steps != steps.to_integral_value():
        raise ValueError("行程必须能整除步距，请检查起点、终点和步距")
    signed_step = spacing if end > start else -spacing
    return start, end, signed_step, int(steps)


def _new_data(start, end, signed_step, expected_count):
    return {
        "schema_version": 1,
        "position_unit": "um",
        "start_position": _json_number(start),
        "end_position": _json_number(end),
        "step": _json_number(abs(signed_step)),
        "expected_count": expected_count,
        "samples": [],
        "complete": False,
    }


def _dump(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def initialize_calibration(start_position, end_position, step):
    """开始新一轮标定：返回空记录，不读取清晰度、不记录起点。"""
    return _dump(_new_data(*_scan_parameters(start_position, end_position, step)))


def append_sharpness(sharpness, start_position, end_position, step,
                     history_json="", reset=False):
    """每次追加一帧，返回 JSON 字符串；由调用方自行保存返回值。

    第 k 帧（k 从 1 开始）：position = start + direction * step * k，单位 um。
    空历史会自动初始化；reset=True 清空历史后记录本帧，不能每帧置 True。
    不兼容旧版仅含 scores 的数据，升级或重新标定时需显式初始化。
    """
    score = _finite_score(sharpness)
    start, end, signed_step, expected_count = _scan_parameters(
        start_position, end_position, step)
    template = _new_data(start, end, signed_step, expected_count)

    if reset or history_json is None or history_json == "":
        data = template
    else:
        if not isinstance(history_json, str):
            raise ValueError("历史数据必须是 String 类型的 JSON 字符串")
        if not history_json.strip():
            data = template
        else:
            try:
                data = json.loads(history_json)
            except ValueError:
                raise ValueError("历史数据不是有效 JSON；请检查变量，不要覆盖历史")

    if not isinstance(data, dict) or not isinstance(data.get("samples"), list):
        raise ValueError("历史数据缺少位置采样列表 samples，请先初始化本轮标定")
    for key in ("schema_version", "position_unit", "start_position",
                "end_position", "step", "expected_count"):
        if isinstance(data.get(key), bool) or data.get(key) != template[key]:
            raise ValueError("历史数据格式或扫描参数发生变化，请先初始化本轮标定：" + key)

    samples = data["samples"]
    if len(samples) >= expected_count:
        raise ValueError("已达到预期采样数，禁止继续追加；新一轮标定请先初始化")
    if data.get("complete") is not False:
        raise ValueError("历史数据的 complete 状态无效，请检查变量")
    for index, sample in enumerate(samples, 1):
        expected_position = _json_number(start + signed_step * index)
        if (not isinstance(sample, dict)
                or isinstance(sample.get("position"), bool)
                or sample.get("position") != expected_position
                or "sharpness" not in sample):
            raise ValueError("历史采样位置或清晰度字段无效，请检查变量")
        sample["sharpness"] = _finite_score(sample["sharpness"])

    frame_number = len(samples) + 1
    position = _json_number(end if frame_number == expected_count
                            else start + signed_step * frame_number)
    samples.append({"position": position, "sharpness": score})
    data["complete"] = len(samples) == expected_count
    return _dump(data)
