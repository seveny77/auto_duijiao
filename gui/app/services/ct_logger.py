# -*- coding: utf-8 -*-
"""CT 统计日志：把各阶段耗时字典格式化输出。"""

import logging


logger = logging.getLogger(__name__)


class CtLogger:
    LABELS = {
        "camera_setup_ms": "相机设置",
        "coarse_ms": "粗扫",
        "coarse_total_ms": "粗扫总计",
        "coarse_collector_start_ms": "采集启动",
        "coarse_motion_ms": "运动链路",
        "coarse_frame_wait_ms": "帧等待",
        "coarse_stabilize_ms": "稳定确认",
        "coarse_stop_ms": "停止取流",
        "ncc_ms": "NCC计算",
        "predict_ms": "策略总计",
        "yolo_ms": "YOLO检测",
        "fine_switch_ms": "精扫切换",
        "fine_ms": "精扫总计",
        "fine_collector_start_ms": "采集启动",
        "fine_motion_ms": "运动链路",
        "fine_frame_wait_ms": "帧等待",
        "fine_stabilize_ms": "稳定确认",
        "fine_stop_ms": "停止取流",
        "final_switch_ms": "恢复全幅",
        "final_collector_start_ms": "采集启动",
        "single_capture_ms": "单点运动",
        "final_frame_wait_ms": "最终帧等待",
        "final_stop_ms": "停止取流",
        "final_hold_ms": "最终回位",
        "final_ms": "定拍总计",
        "cal_flyscan_ms": "标定飞拍",
        "cal_collector_start_ms": "采集启动",
        "cal_motion_ms": "运动链路",
        "cal_frame_wait_ms": "帧等待",
        "cal_stabilize_ms": "稳定确认",
        "cal_stop_ms": "停止取流",
        "eval_ms": "图片评价",
        "template_ms": "模板生成",
        "sim_total_ms": "模拟总耗时",
        "total_ms": "总耗时",
    }

    GROUPS = (
        (
            "CT 总览",
            (
                "camera_setup_ms",
                "predict_ms",
                "yolo_ms",
                "fine_switch_ms",
                "fine_ms",
                "final_ms",
                "cal_flyscan_ms",
                "template_ms",
                "eval_ms",
                "sim_total_ms",
                "total_ms",
            ),
        ),
        (
            "CT 粗扫",
            (
                "coarse_total_ms",
                "coarse_collector_start_ms",
                "coarse_motion_ms",
                "coarse_frame_wait_ms",
                "coarse_stabilize_ms",
                "coarse_stop_ms",
                "ncc_ms",
            ),
        ),
        (
            "CT 精扫",
            (
                "fine_collector_start_ms",
                "fine_motion_ms",
                "fine_frame_wait_ms",
                "fine_stabilize_ms",
                "fine_stop_ms",
            ),
        ),
        (
            "CT 定拍",
            (
                "final_switch_ms",
                "final_collector_start_ms",
                "single_capture_ms",
                "final_frame_wait_ms",
                "final_stop_ms",
                "final_hold_ms",
            ),
        ),
        (
            "CT 标定",
            (
                "cal_collector_start_ms",
                "cal_motion_ms",
                "cal_frame_wait_ms",
                "cal_stabilize_ms",
                "cal_stop_ms",
            ),
        ),
    )

    def log(self, ct: dict):
        """输出一行 CT 阶段耗时摘要。"""

        if not ct:
            return

        for group_name, keys in self.GROUPS:
            parts = [
                f"{self.LABELS[key]} {ct[key]:.1f}ms"
                for key in keys
                if key in ct
            ]

            if parts:
                logger.info(
                    "%s | %s",
                    group_name,
                    " | ".join(parts),
                )
