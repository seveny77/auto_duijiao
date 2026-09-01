# -*- coding: utf-8 -*-
"""VA 精扫：规划对称拍照位置，逐帧记录，最终选择实拍清晰度最高的帧。

独立使用 Python 标准库，不读写软件变量、不保存图像、不控制运动。
固定从低坐标向高坐标扫描，起点不触发、终点触发；位置单位 um。
"""

import json
import math
from decimal import Decimal, InvalidOperation


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


def _dump(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _new_plan(predicted_peak_um, step, side_count, travel_min_um, travel_max_um):
    center = _decimal(predicted_peak_um, "预测中心位置")
    spacing = _decimal(step, "精扫步距")
    side = _decimal(side_count, "单边拍照点数")
    if spacing <= 0:
        raise ValueError("精扫步距必须大于 0")
    if side < 0 or side != side.to_integral_value():
        raise ValueError("单边拍照点数必须是非负整数")
    side = int(side)

    first_sample = center - spacing * side
    last_sample = center + spacing * side
    # 起点不拍照，因此必须在第一拍之前额外预留一个步距。
    motion_start = first_sample - spacing
    lower = None if travel_min_um is None else _decimal(travel_min_um, "允许最小位置")
    upper = None if travel_max_um is None else _decimal(travel_max_um, "允许最大位置")
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError("允许最小位置必须小于允许最大位置")
    if lower is not None and motion_start < lower:
        raise ValueError("精扫运动起点低于允许最小位置，请减少点数、步距或调整中心")
    if upper is not None and last_sample > upper:
        raise ValueError("精扫运动终点高于允许最大位置，请减少点数、步距或调整中心")

    return {
        "schema_version": 1,
        "scan_type": "fine",
        "position_unit": "um",
        "center_position": _json_number(center),
        "side_count": side,
        "step": _json_number(spacing),
        "start_position": _json_number(motion_start),
        "end_position": _json_number(last_sample),
        "first_sample_position": _json_number(first_sample),
        "last_sample_position": _json_number(last_sample),
        "expected_count": 2 * side + 1,
        "center_frame_index": side,
        "travel_min_um": None if lower is None else _json_number(lower),
        "travel_max_um": None if upper is None else _json_number(upper),
    }


def _best_fields(samples, plan):
    if not samples:
        return {"best_frame_index": None, "best_frame_number": None,
                "best_position_um": None, "best_sharpness": None,
                "best_at_boundary": False}
    center = Decimal(str(plan["center_position"]))
    # 清晰度严格优先；只有完全相等才比较离中心的距离，再比较采集顺序。
    best = max(samples, key=lambda sample: (
        sample["sharpness"],
        -abs(Decimal(str(sample["position"])) - center),
        -sample["frame_index"],
    ))
    return {
        "best_frame_index": best["frame_index"],
        "best_frame_number": best["frame_number"],
        "best_position_um": best["position"],
        "best_sharpness": best["sharpness"],
        "best_at_boundary": best["frame_index"] in (0, plan["expected_count"] - 1),
    }


def initialize_fine_scan(predicted_peak_um, step, side_count,
                         travel_min_um=None, travel_max_um=None):
    """返回精扫计划和空记录的 JSON 字符串，只在运动前调用一次。

    左右各 side_count 点，中心也拍，共 2*side_count+1 帧。
    拍照范围：[center-side_count*step, center+side_count*step]。
    运动范围：[center-(side_count+1)*step, center+side_count*step]。
    可传设备允许的行程上下限；不传的那一侧不会进行行程限位校验。
    超限时直接报错，不擅自裁剪区间或改变要求的点数。
    """
    data = _new_plan(predicted_peak_um, step, side_count, travel_min_um, travel_max_um)
    data.update({"samples": [], "complete": False, "current_is_best": False})
    data.update(_best_fields([], data))
    return _dump(data)


def _read_history(history_json):
    if not isinstance(history_json, str) or not history_json.strip():
        raise ValueError("精扫历史必须是非空 JSON 字符串，请先初始化")
    try:
        data = json.loads(history_json)
    except ValueError:
        raise ValueError("精扫历史不是有效 JSON")
    if (not isinstance(data, dict) or data.get("scan_type") != "fine"
            or not isinstance(data.get("samples"), list)):
        raise ValueError("历史数据不是精扫记录，请检查变量")
    plan = _new_plan(data.get("center_position"), data.get("step"), data.get("side_count"),
                     data.get("travel_min_um"), data.get("travel_max_um"))
    for key, expected in plan.items():
        if key not in data or isinstance(data[key], bool) or data[key] != expected:
            raise ValueError("精扫计划字段损坏或不一致：" + key)
    samples = data["samples"]
    if len(samples) > plan["expected_count"]:
        raise ValueError("精扫记录超过计划帧数")
    if data.get("complete") is not (len(samples) == plan["expected_count"]):
        raise ValueError("精扫完成标志与已采帧数不一致")
    start = Decimal(str(plan["start_position"]))
    spacing = Decimal(str(plan["step"]))
    for index, sample in enumerate(samples):
        if (not isinstance(sample, dict)
                or type(sample.get("frame_index")) is not int or sample["frame_index"] != index
                or type(sample.get("frame_number")) is not int or sample["frame_number"] != index + 1
                or isinstance(sample.get("position"), bool)
                or sample.get("position") != _json_number(start + spacing * (index + 1))):
            raise ValueError("精扫帧序号或位置与计划不一致")
        sample["sharpness"] = _finite_score(sample.get("sharpness"))
    # 最高分帧由样本重新求得，不信任外部修改的缓存结果字段。
    data.update(_best_fields(samples, plan))
    data["current_is_best"] = bool(samples) and data["best_frame_index"] == len(samples) - 1
    return data


def append_fine_sharpness(sharpness, history_json):
    """每帧计算清晰度后调用一次，返回更新后的 JSON 字符串。

    扫描参数固定使用初始化记录，不允许在扫描中途重新传参改变计划。
    current_is_best=True 表示应由调用方将当前图像复制/保存为目前最佳图像。
    本脚本只保存帧号、位置和评分，不保存任何图像像素。
    """
    score = _finite_score(sharpness)
    data = _read_history(history_json)
    if data["complete"]:
        raise ValueError("精扫已完成，禁止继续追加；下一轮请先初始化")
    index = len(data["samples"])
    position = _json_number(Decimal(str(data["start_position"]))
                            + Decimal(str(data["step"])) * (index + 1))
    data["samples"].append({"frame_index": index, "frame_number": index + 1,
                            "position": position, "sharpness": score})
    data.update(_best_fields(data["samples"], data))
    data["current_is_best"] = data["best_frame_index"] == index
    data["complete"] = len(data["samples"]) == data["expected_count"]
    return _dump(data)


def finalize_fine_scan(history_json):
    """采完后返回最终最高分帧的 JSON；不拟合、不重新 NCC、不要求补拍。

    complete 仅表示所有计划帧已记录并完成选择，不保证这是全行程最佳焦点。
    最优帧落在精扫边缘时只标记 best_at_boundary，不阻止用户结束本轮流程。
    """
    data = _read_history(history_json)
    if not data["complete"]:
        raise ValueError("精扫尚未采完，不能输出最终结果")
    result = {
        "schema_version": 1, "scan_type": "fine_result", "position_unit": "um",
        "complete": True, "sample_count": len(data["samples"]),
        "center_position": data["center_position"],
        "first_sample_position": data["first_sample_position"],
        "last_sample_position": data["last_sample_position"],
        "selection_rule": "max_sharpness_then_nearest_center_then_earlier_frame",
    }
    result.update(_best_fields(data["samples"], data))
    return _dump(result)
