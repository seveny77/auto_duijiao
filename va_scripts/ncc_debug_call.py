# -*- coding: utf-8 -*-
"""VA 联调调用模板：放在 NCC 函数定义之后，替换原来的调用及 raise 判断。

由调用方提前准备 calibration_json、coarse_json，并定义 calculate_coarse_ncc。
软件变量读写由调用方添加。此文件是调用片段，不是独立算法模块。
"""

import json
import math


DEBUG_MODE = True  # 联调时 True；正式运行改回 False。

ncc_result_json = calculate_coarse_ncc(calibration_json, coarse_json)
result = json.loads(ncc_result_json)

# 数据损坏、未采完等输入错误仍由上面的计算函数抛出，不进行忽略。
if not result["valid"]:
    if not DEBUG_MODE:
        raise ValueError(result["message"])
    print("[联调模式] 忽略匹配质量拦截：", result["quality"], result["message"])

# 有 NCC 候选就用候选跑流程；完全无候选时仅在联调模式下使用粗扫实测最高点。
predicted_peak_um = result["predicted_peak_um"]
position_source = "ncc_candidate"
if predicted_peak_um is None:
    if not DEBUG_MODE:
        raise ValueError("没有可用的 NCC 预测位置")
    predicted_peak_um = result["coarse_best_position_um"]
    position_source = "coarse_best_fallback"
    print("[联调模式] 无 NCC 候选，改用粗扫实测最高点：", predicted_peak_um)

# 只允许输出本轮粗扫行程内的有限位置；这不能替代 PLC/电机的机械限位。
predicted_peak_um = float(predicted_peak_um)
coarse_data = json.loads(coarse_json)
scan_min = min(float(coarse_data["start_position"]), float(coarse_data["end_position"]))
scan_max = max(float(coarse_data["start_position"]), float(coarse_data["end_position"]))
if not math.isfinite(predicted_peak_um) or not scan_min <= predicted_peak_um <= scan_max:
    raise ValueError("输出位置无效或超出本轮粗扫行程，停止流程")

# 原始 valid、quality、predicted_peak_um、NCC 分数均保留，不伪装成匹配通过。
ncc_score = result["ncc_max"]  # 无 NCC 候选时为 None，不要直接写入数值型软件变量。
shift_um = result["shift_um"]  # 无 NCC 候选时为 None。
result["debug_mode"] = DEBUG_MODE
result["flow_continue"] = True
result["flow_peak_um"] = predicted_peak_um
result["flow_position_source"] = position_source
ncc_result_json = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

print("流程使用的位置（um）：", predicted_peak_um, "NCC：", ncc_score)
# 由你保存 ncc_result_json，并将 predicted_peak_um 传给下游流程。
# 下游精扫范围仍需限制在设备允许行程内；不要再用原来的 if not valid: raise 二次拦截。
