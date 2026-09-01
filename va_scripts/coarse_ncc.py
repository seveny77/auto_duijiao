# -*- coding: utf-8 -*-
"""用完整粗扫 JSON 与标定 JSON 进行一维平移 NCC，返回结果 JSON 字符串。

独立使用标准库，不导入其他项目文件，不读写软件变量，不控制运动。
模型：coarse(z) ≈ a * calibration(z - shift) + b，其中 a > 0。
"""

import json
import math
from bisect import bisect_left
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR


def _number(value, name):
    if isinstance(value, bool):
        raise ValueError(name + "不能是布尔值")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(name + "必须是数值")
    if not math.isfinite(result):
        raise ValueError(name + "必须是有限数值")
    return result


def _reject_constant(value):
    raise ValueError("JSON 中不能包含 " + value)


def _read_scan(text, name):
    """校验当前标定/粗扫数据格式，并将采样点按绝对位置升序排列。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(name + "必须是非空 JSON 字符串")
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        raise ValueError(name + "不是有效 JSON")
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1 or data.get("position_unit") != "um"):
        raise ValueError(name + "格式版本或位置单位不正确")
    try:
        start = Decimal(str(data["start_position"]))
        end = Decimal(str(data["end_position"]))
        step = Decimal(str(data["step"]))
    except (KeyError, InvalidOperation, ValueError):
        raise ValueError(name + "缺少有效的起终点或步距")
    if (not all(value.is_finite() for value in (start, end, step))
            or step <= 0 or start == end):
        raise ValueError(name + "扫描参数无效")
    count = abs(end - start) / step
    if count != count.to_integral_value():
        raise ValueError(name + "行程不能整除步距")
    expected_count = data.get("expected_count")
    if type(expected_count) is not int or expected_count != int(count):
        raise ValueError(name + "预期采样数量与扫描参数不一致")
    samples = data.get("samples")
    if not isinstance(samples, list):
        raise ValueError(name + "缺少 samples 列表")
    if data.get("complete") is not True or len(samples) != expected_count:
        raise ValueError(name + "尚未完整采集，不能计算 NCC")

    signed_step = step if end > start else -step
    pairs = []
    for index, sample in enumerate(samples, 1):
        if not isinstance(sample, dict):
            raise ValueError(name + "采样点格式无效")
        position = _number(sample.get("position"), name + "采样位置")
        score = _number(sample.get("sharpness"), name + "清晰度")
        expected_position = float(end if index == expected_count
                                  else start + signed_step * index)
        if position != expected_position:
            raise ValueError(name + "采样位置与帧序号不一致")
        pairs.append((position, score))
    pairs.sort()
    positions = [pair[0] for pair in pairs]
    if any(a >= b for a, b in zip(positions, positions[1:])):
        raise ValueError(name + "采样位置重复或精度不足")
    spacing = _number(step, name + "步距")
    if spacing <= 0:
        raise ValueError(name + "步距超出浮点数精度范围")
    return data, positions, [pair[1] for pair in pairs], spacing


def _centered_unit(values):
    """缩放后去均值、单位化；平坦曲线没有定义良好的 NCC。"""
    scale = max(abs(value) for value in values)
    if scale == 0:
        return None
    scaled = [value / scale for value in values]
    if max(scaled) - min(scaled) <= 1e-12:
        return None
    mean = math.fsum(scaled) / len(scaled)
    centered = [value - mean for value in scaled]
    norm = math.sqrt(math.fsum(value * value for value in centered))
    return [value / norm for value in centered]


def _interpolate(positions, scores, position):
    """只在标定覆盖区间内线性插值；不补零、不外推。"""
    tolerance = max(1e-10, max(abs(positions[0]), abs(positions[-1])) * 2e-15)
    if position < positions[0] - tolerance or position > positions[-1] + tolerance:
        return None
    position = min(positions[-1], max(positions[0], position))
    index = bisect_left(positions, position)
    if index == 0 or positions[index] == position:
        return scores[index]
    left = index - 1
    weight = (position - positions[left]) / (positions[index] - positions[left])
    return (1.0 - weight) * scores[left] + weight * scores[index]


def calculate_coarse_ncc(calibration_json, coarse_json, match_step_um=None,
                         min_ncc=0.9, min_points=5, min_overlap_ratio=0.6,
                         min_ncc_gap=0.02, max_peak_valley_ratio=0.05):
    """粗扫结束后调用，返回 str；读取结果的 valid 后再决定是否进入精扫。

    match_step_um: 候选偏移分辨率，默认标定步距；不是粗扫步距。
    min_points: 每个候选至少有多少对采样，默认 5，不能小于 3。
    min_overlap_ratio: 有效配对数 / 粗扫总点数的下限，默认 0.6。
    min_ncc_gap: 与距离至少一个粗扫步距的另一个候选之间的 NCC 差下限。
    max_peak_valley_ratio: 多个最大值之间的凹陷 / 标定全曲线极差的上限。
        默认 0.05；浅凹陷按同一宽峰顶处理，取最左/最右最大值位置的中点。
        深凹陷仍按分离多峰拒绝；中点只是粗扫参考，结果同时给出峰顶范围。
    所有阈值都是初始工程参数，不表示统计概率，需要现场数据验证。

    输入损坏/未采完抛出 ValueError；无可靠匹配返回 valid=False 和原因。
    predicted_peak_um 即使存在也只是粗扫估计，仍需后续精扫或补拍验证。
    """
    if type(min_points) is not int or min_points < 3:
        raise ValueError("min_points 必须是大于等于 3 的整数")
    threshold = _number(min_ncc, "min_ncc")
    overlap_limit = _number(min_overlap_ratio, "min_overlap_ratio")
    gap_limit = _number(min_ncc_gap, "min_ncc_gap")
    valley_limit = _number(max_peak_valley_ratio, "max_peak_valley_ratio")
    if not 0 <= threshold <= 1 or not 0 < overlap_limit <= 1 or not 0 <= gap_limit <= 2:
        raise ValueError("NCC、重叠比例或差值阈值超出范围")
    if not 0 <= valley_limit < 1:
        raise ValueError("max_peak_valley_ratio 必须在 [0, 1) 范围内")

    cal, tp, ts, cal_step = _read_scan(calibration_json, "标定数据")
    coarse, cp, cs, coarse_step = _read_scan(coarse_json, "粗扫数据")
    if coarse.get("scan_type") != "coarse":
        raise ValueError("粗扫数据缺少 scan_type=coarse，请使用粗扫记录函数")
    if cal.get("scan_type") not in (None, "calibration"):
        raise ValueError("标定数据不能使用粗扫数据代替")
    resolution = cal_step if match_step_um is None else _number(match_step_um, "匹配步距")
    if resolution <= 0:
        raise ValueError("匹配步距必须大于 0")
    lower = min(_number(coarse["start_position"], "粗扫起点"),
                _number(coarse["end_position"], "粗扫终点"))
    upper = max(_number(coarse["start_position"], "粗扫起点"),
                _number(coarse["end_position"], "粗扫终点"))
    maximum = max(ts)
    peak_indices = [index for index, score in enumerate(ts) if score == maximum]
    peak_first, peak_last = peak_indices[0], peak_indices[-1]
    peak_range = [tp[peak_first], tp[peak_last]]
    template_peak = peak_range[0] / 2.0 + peak_range[1] / 2.0
    observed_index = max(range(len(cs)), key=lambda index: cs[index])
    required = max(min_points, int(math.ceil(overlap_limit * len(cp))))
    result = {
        "schema_version": 1, "position_unit": "um", "method": "shift_zncc",
        "valid": False, "quality": "no_match", "message": "",
        "predicted_peak_um": None, "shift_um": None, "ncc_max": None,
        "template_peak_um": template_peak,
        "template_peak_range_um": peak_range,
        "template_peak_count": len(peak_indices),
        "template_peak_method": "single_maximum" if len(peak_indices) == 1 else "plateau_midpoint",
        "template_peak_valley_ratio": None,
        "predicted_peak_range_um": None,
        "coarse_best_position_um": cp[observed_index],
        "coarse_best_sharpness": cs[observed_index],
        "matched_points": 0, "coarse_count": len(cp), "overlap_ratio": 0.0,
        "required_points": required, "match_step_um": resolution,
        "second_peak_um": None, "second_ncc": None, "ncc_gap": None,
        "valid_candidates": 0,
    }

    def finish(quality, message):
        result["quality"] = quality
        result["message"] = message
        result["valid"] = quality == "ok"
        return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    if len(cp) < required or len(tp) < 3:
        return finish("insufficient_points", "采样点不足，请减小粗扫步距或扩大扫描范围")
    if _centered_unit(ts) is None or _centered_unit(cs) is None:
        return finish("flat_curve", "标定或粗扫曲线几乎无变化，无法计算有效 NCC")
    # 使用归一化极差判断峰间凹陷，避免整数化的相邻峰顶被一律当成分离多峰。
    # 不修改/平滑原始曲线，不人为打破清晰度相等的关系。
    template_scale = max(abs(score) for score in ts)
    template_values = [score / template_scale for score in ts]
    dynamic_range = max(template_values) - min(template_values)
    valley_ratio = (max(template_values) - min(template_values[peak_first:peak_last + 1])) / dynamic_range
    result["template_peak_valley_ratio"] = valley_ratio
    if len(peak_indices) > 1 and valley_ratio > valley_limit + 1e-12:
        result["template_peak_method"] = "unresolved_multiple_peaks"
        result["template_peak_um"] = None
        return finish("ambiguous_template", "标定多个最大值之间存在明显低谷，无法确定单一焦点")
    if peak_first == 0 or peak_last == len(tp) - 1:
        return finish("template_boundary", "标定峰位于采样边缘，请扩大标定范围")

    # 候选偏移是 resolution 的整数倍，保证包含零偏移；预测峰不能超出粗扫行程。
    quantum = Decimal(str(resolution))
    peak_decimal = Decimal(str(template_peak))
    first = int(((Decimal(str(lower)) - peak_decimal) / quantum).to_integral_value(
        rounding=ROUND_CEILING))
    last = int(((Decimal(str(upper)) - peak_decimal) / quantum).to_integral_value(
        rounding=ROUND_FLOOR))
    if last - first + 1 > 100000:
        raise ValueError("候选数量超过 100000，请增大 match_step_um 或缩小粗扫行程")
    # 模板已在插值前缩放，NCC 不受正比例缩放影响，且可避免极大数插值溢出。
    candidates = []
    for index in range(first, last + 1):
        delta = float(quantum * index)
        predicted = float(peak_decimal + quantum * index)
        x, y, paired_positions = [], [], []
        for position, score in zip(cp, cs):
            value = _interpolate(tp, template_values, position - delta)
            if value is not None:
                x.append(score)
                y.append(value)
                paired_positions.append(position)
        if len(x) < required:
            continue
        nx, ny = _centered_unit(x), _centered_unit(y)
        if nx is None or ny is None:
            continue
        correlation = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(nx, ny))))
        candidates.append({
            "ncc": correlation, "shift": delta, "peak": predicted, "count": len(x),
            "bracketed": (paired_positions[0] < peak_range[0] + delta
                          and peak_range[1] + delta < paired_positions[-1]),
        })

    result["valid_candidates"] = len(candidates)
    if not candidates:
        return finish("no_match", "没有足够重叠且非平坦的候选曲线，请检查扫描范围及曲线")
    # NCC 相同时优先选重叠点多的候选；歧义仍由后面的间隔检查报告。
    best = max(candidates, key=lambda item: (item["ncc"], item["count"], -abs(item["shift"])))
    result.update({
        "predicted_peak_um": best["peak"], "shift_um": best["shift"],
        "predicted_peak_range_um": [float(Decimal(str(position)) + Decimal(str(best["shift"])))
                                    for position in peak_range],
        "ncc_max": best["ncc"], "matched_points": best["count"],
        "overlap_ratio": best["count"] / len(cp),
    })
    separated = [item for item in candidates
                 if abs(item["peak"] - best["peak"]) >= coarse_step - 1e-10]
    if separated:
        second = max(separated, key=lambda item: item["ncc"])
        result.update({"second_peak_um": second["peak"], "second_ncc": second["ncc"],
                       "ncc_gap": best["ncc"] - second["ncc"]})
    if best["ncc"] < threshold:
        return finish("low_ncc", "NCC 未达到阈值，请检查标定适用性或补充扫描")
    if (not best["bracketed"] or best["peak"] - lower < coarse_step
            or upper - best["peak"] < coarse_step
            or result["predicted_peak_range_um"][0] <= lower
            or result["predicted_peak_range_um"][1] >= upper):
        return finish("boundary", "预测峰靠近扫描边界或缺少峰两侧数据，需扩展扫描验证")
    if result["ncc_gap"] is not None and result["ncc_gap"] < gap_limit:
        return finish("ambiguous", "不同焦点位置的相关性接近，粗扫不足以区分，需补拍验证")
    if len(peak_indices) > 1:
        return finish("ok", "NCC 匹配通过；预测位置是宽峰顶中点，精扫需覆盖预测峰顶范围并验证")
    return finish("ok", "NCC 匹配通过，可将预测位置用于后续精扫中心，仍需验证最终焦点")
