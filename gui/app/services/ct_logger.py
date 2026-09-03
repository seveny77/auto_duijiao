# -*- coding: utf-8 -*-
"""CT 统计日志：把各阶段耗时字典格式化输出。

搜索结果按「五大块」表格输出（阶段/耗时/占比/内容，口径与
docs/对焦CT拆解报告.md §1 一致，行内是 markdown 表格，可直接粘贴进文档）；
标定/模拟等其它耗时候选字典退回逐项一行的旧格式。
"""

import logging


logger = logging.getLogger(__name__)


class CtLogger:
    # 搜索五大块：耗时键（可多键求和）→ 阶段行；items 为内容列分项（缺键跳过）。
    # 注意：精扫停流（fine_stop_ms）计时上归属单点取证段（pipeline 先停精扫流
    # 再切相机），故分项挂在"单点取证+回位"下，保证内容列合计≈耗时列。
    STAGES = (
        (
            "粗扫+NCC预测",
            ("predict_ms",),
            (
                ("采集", "coarse_collector_start_ms"),
                ("运动", "coarse_motion_ms"),
                ("等帧", "coarse_frame_wait_ms"),
                ("稳定", "coarse_stabilize_ms"),
                ("停流", "coarse_stop_ms"),
                ("NCC", "ncc_ms"),
            ),
        ),
        (
            "精扫",
            ("fine_ms",),
            (
                ("采集", "fine_collector_start_ms"),
                ("运动", "fine_motion_ms"),
                ("等帧", "fine_frame_wait_ms"),
                ("稳定", "fine_stabilize_ms"),
            ),
        ),
        (
            "单点取证+回位",
            ("final_ms",),
            (
                ("停精扫流", "fine_stop_ms"),
                ("恢复初始工作窗口", "final_switch_ms"),
                ("采集", "final_collector_start_ms"),
                ("单点运动", "single_capture_ms"),
                ("等帧", "final_frame_wait_ms"),
                ("停流", "final_stop_ms"),
                ("回位", "final_hold_ms"),
            ),
        ),
        (
            "相机参数下发",
            ("camera_setup_ms",),
            (),
        ),
        (
            "段间衔接",
            ("yolo_ms", "fine_switch_ms"),
            (
                ("YOLO", "yolo_ms"),
                ("精扫相机切换", "fine_switch_ms"),
            ),
        ),
    )

    # 无分项可列的行的固定文案。
    STATIC_CONTENT = {
        "相机参数下发": "曝光/增益/decimation/ROI（句柄常驻）",
    }

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
        "final_switch_ms": "恢复初始工作窗口",
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
        """输出 CT 阶段耗时摘要：搜索走五大块表格，其它走逐项行。"""

        if not ct:
            return

        if "total_ms" in ct and "predict_ms" in ct:
            self._log_search_table(ct)
        else:
            self._log_flat(ct)

    def _log_search_table(self, ct: dict):
        """搜索结果：五大块 markdown 表格（口径同拆解报告 §1）。"""

        total = ct["total_ms"]
        rows = []
        for name, ms_keys, items in self.STAGES:
            if not any(key in ct for key in ms_keys):
                continue
            ms = sum(ct[key] for key in ms_keys if key in ct)
            content = " + ".join(
                f"{label} {ct[key]:.1f}"
                for label, key in items
                if key in ct
            )
            rows.append((ms, name, content or self.STATIC_CONTENT.get(name, "-")))

        # 按耗时降序，与报告 §1 的五大块表一致。
        rows.sort(key=lambda row: -row[0])

        lines = [
            f"CT 总览（总耗时 {total:.1f}ms）",
            "| 阶段 | 耗时 | 占比 | 内容 |",
            "|---|---:|---:|---|",
        ]
        block_total = 0.0
        for ms, name, content in rows:
            block_total += ms
            lines.append(
                f"| {name} | {ms:.1f}ms | {ms / total * 100:.1f}% | {content} |"
            )

        residual = total - block_total
        if residual >= 0.5:
            lines.append(
                f"（五块合计 {block_total:.1f}ms，与总耗时差 {residual:.1f}ms"
                " 为模板加载等零星项）"
            )

        logger.info("%s", "\n".join(lines))

    def _log_flat(self, ct: dict):
        """标定/模拟等其它口径：按组各输出一行。"""

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
