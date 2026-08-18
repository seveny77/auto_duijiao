# -*- coding: utf-8 -*-
"""CT 统计日志：把各阶段耗时 dict 格式化输出。"""


class CtLogger:
    LABELS = {
        "plc_connect_ms": "PLC连接",
        "camera_setup_ms": "相机设置",
        "coarse_ms": "粗扫",
        "ncc_ms": "NCC预测",
        "yolo_ms": "YOLO检测",
        "fine_switch_ms": "精扫切换",
        "fine_ms": "精扫",
        "final_ms": "定拍",
        "cal_flyscan_ms": "标定飞拍",
        "eval_ms": "图片评价",
        "template_ms": "模板生成",
        "sim_total_ms": "模拟总耗时",
        "total_ms": "总耗时",
    }

    def __init__(self, log_fn):
        self._log = log_fn

    def log(self, ct: dict):
        if not ct:
            return
        parts = []
        for key, label in self.LABELS.items():
            if key in ct:
                parts.append(f"{label} {ct[key]:.0f}ms")
        self._log("CT | " + " | ".join(parts))