# -*- coding: utf-8 -*-
"""搜索与标定任务启动编排服务。"""

import logging
import os


logger = logging.getLogger(__name__)


class FocusRunService:
    """准备任务配置和运行期资源，然后提交后台任务。"""

    def __init__(
            self,
            config_service,
            controller,
            focus_task_service,
            live_view_service,
            result_presenter,
            detection_model_service,
            stroke_range_fn,
            motion_backend_fn,
            motion_state_fn,
            confirm_fn,
            status_fn,
    ):
        self._config_service = config_service
        self._controller = controller
        self._focus_task_service = focus_task_service
        self._live_view_service = live_view_service
        self._result_presenter = result_presenter
        self._detection_model_service = detection_model_service
        self._stroke_range_fn = stroke_range_fn
        self._motion_backend_fn = motion_backend_fn
        self._motion_state_fn = motion_state_fn
        self._confirm_fn = confirm_fn
        self._status_fn = status_fn

    def start(self) -> bool:
        """校验配置、准备运行期资源，然后启动搜索或标定任务。"""

        if self._live_view_service.is_active or self._live_view_service.is_running:
            if not self._live_view_service.stop():
                logger.warning("实时预览尚未完全停止，暂不能启动任务")
                self._status_fn("请等待实时预览停止后再启动任务")
                return False

        # 先收集参数，暂时不要切换界面状态。
        cfg = self._config_service.build_focus_config()
        if cfg.mode == "real":
            # MotionService拥有连接的生命周期；后台任务只借用它。
            cfg.motion_backend = self._motion_backend_fn()

        # 在创建后台线程、连接相机和触发运动之前校验参数。
        errors, warnings = self._validate_config(cfg)

        for message in warnings:
            logger.warning("参数提醒: %s", message)

        if errors:
            for message in errors:
                logger.error("参数错误: %s", message)

            self._status_fn(errors[0])
            return False

        if cfg.mode == "real" and not cfg.yes:
            if cfg.action == "calibrate":
                action_name = "图像标定"
                motion_description = "丝杆将执行标定全扫"
            else:
                action_name = "搜索对焦"
                motion_description = "丝杆将执行粗扫、精扫和定点移动"

            confirmed = self._confirm_fn(
                f"即将执行真实{action_name}。\n\n"
                f"{motion_description}，Z 轴会发生运动。\n\n"
                "请确认设备周围安全，是否继续？"
            )

            if not confirmed:
                logger.info("用户取消真实%s", action_name)
                self._status_fn("用户取消执行")
                return False

            cfg.yes = True

        # 参数通过以后，再清空上一轮展示内容。
        self._result_presenter.begin_task()

        action_labels = {
            "search": "搜索对焦",
            "calibrate": "图像标定",
        }
        mode_labels = {
            "real": "真实",
            "sim": "仿真",
        }

        logger.info(
            "开始执行: 动作=%s, 模式=%s",
            action_labels.get(cfg.action, cfg.action),
            mode_labels.get(cfg.mode, cfg.mode),
        )

        cfg.cancel_event = self._controller.new_cancel_event()
        cfg.detect_model_obj = self._detection_model_service.model

        self._controller.set_state(self._controller.STATE_RUNNING)

        if self._focus_task_service.start(cfg):
            return True

        self._controller.set_state(self._controller.STATE_ERROR)
        logger.error("搜索/标定后台任务启动失败")
        self._status_fn("后台任务启动失败")
        return False

    def _validate_config(self, cfg):
        """
        校验一次任务的静态参数。

        返回:
            errors: 必须阻止任务启动的问题
            warnings: 允许继续运行，但需要提醒的问题
        """

        errors = []
        warnings = []

        # -------------------------------------------------
        # 1. 计算本轮真正使用的运动范围
        # -------------------------------------------------
        if cfg.action == "calibrate":
            start_um = (
                cfg.calibrate_start_um
                if cfg.calibrate_start_um is not None
                else cfg.search_start_um
            )
            span_um = (
                cfg.calibrate_span_um
                if cfg.calibrate_span_um is not None
                else cfg.search_span_um
            )
            step_um = cfg.calibrate_step_um
            phase_name = "标定"
            requires_scan = True

        elif cfg.strategy == "ncc":
            start_um = cfg.search_start_um
            span_um = cfg.search_span_um
            step_um = cfg.coarse_step_um
            phase_name = "NCC 搜索"
            requires_scan = True

        else:
            start_um = cfg.search_start_um
            span_um = cfg.search_span_um
            step_um = None
            phase_name = "AI 搜索"
            requires_scan = False

        end_um = start_um + span_um

        # -------------------------------------------------
        # 2. 基本数值检查
        # -------------------------------------------------
        if span_um <= 0:
            errors.append(
                f"{phase_name}跨度必须大于 0"
            )

        if requires_scan:
            if step_um <= 0:
                errors.append(
                    f"{phase_name}步距必须大于 0"
                )

            if step_um > 0 and span_um > 0:
                frame_count = span_um // step_um

                if frame_count < 3:
                    errors.append(
                        f"{phase_name}帧数只有 "
                        f"{frame_count} 张，"
                        "至少需要 3 张"
                    )

                if span_um % step_um != 0:
                    errors.append(
                        f"{phase_name}跨度 "
                        f"{span_um} μm "
                        f"不能被步距 "
                        f"{step_um} μm 整除"
                    )

        # -------------------------------------------------
        # 3. 运动控制器连接与行程检查
        # -------------------------------------------------
        stroke_range = self._stroke_range_fn()

        if cfg.mode == "real":
            motion = cfg.motion_backend
            if motion is None:
                errors.append("请先连接M60 + E4O4运动控制器")
            else:
                try:
                    state = self._motion_state_fn()
                except Exception as error:
                    errors.append(
                        f"读取运动控制器状态失败: {error}"
                    )
                else:
                    if not state.connected:
                        errors.append("运动控制器已断开，请重新连接")
                    elif not state.homed:
                        errors.append("本次连接尚未回原点，请先点击“回原点”")
                    elif not state.servo_enabled:
                        errors.append("伺服未使能，请先手动使能")
                    elif state.alarm:
                        errors.append("轴卡报警未清除，请先复位报警")
                    elif state.emergency_stop:
                        errors.append("急停信号有效，无法执行自动对焦")
                    elif state.positive_limit or state.negative_limit:
                        errors.append("当前处于硬限位，无法执行自动对焦")
                    elif state.offline:
                        errors.append("伺服轴掉线，无法执行自动对焦")
                    elif not state.ready_for_autofocus:
                        errors.append(state.message or "运动控制器尚未就绪")

        if cfg.mode == "real" and stroke_range is not None:
            stroke_min, stroke_max = stroke_range

            if start_um < stroke_min:
                errors.append(
                    f"{phase_name}起点 {start_um} μm "
                    f"小于轴卡软件限位 {stroke_min} μm"
                )

            if end_um > stroke_max:
                errors.append(
                    f"{phase_name}终点 {end_um} μm "
                    f"大于轴卡软件限位 {stroke_max} μm"
                )

        elif cfg.mode == "real":
            warnings.append(
                "尚未通过GUI读取轴卡行程，"
                "本次无法提前校验扫描范围"
            )

        # -------------------------------------------------
        # 4. 搜索任务专用检查
        # -------------------------------------------------
        if cfg.action == "search":
            if cfg.strategy == "ncc":
                if cfg.fine_step_um > cfg.coarse_step_um:
                    warnings.append(
                        f"精扫步距 {cfg.fine_step_um} μm "
                        f"大于粗扫步距 "
                        f"{cfg.coarse_step_um} μm"
                    )

                self._validate_template(
                    cfg.template,
                    errors,
                    warnings,
                )

            elif cfg.strategy == "dl":
                self._validate_dl_config(
                    cfg,
                    errors,
                    warnings,
                )

            else:
                errors.append(
                    f"不支持的搜索策略: {cfg.strategy}"
                )

        return errors, warnings

    @staticmethod
    def _validate_dl_config(
            cfg,
            errors,
            warnings,
    ):
        """检查 AI 单帧对焦所需的静态参数。"""

        if not cfg.dl_model:
            errors.append("AI 对焦前必须选择模型文件")

        elif not os.path.isfile(cfg.dl_model):
            errors.append(
                f"AI 对焦模型不存在: {cfg.dl_model}"
            )

        if cfg.shot_position_um is None:
            errors.append(
                "AI 对焦前必须设置单帧拍摄位置"
            )
            return

        search_end_um = (
            cfg.search_start_um
            + cfg.search_span_um
        )

        if not (
                cfg.search_start_um
                <= cfg.shot_position_um
                <= search_end_um
        ):
            errors.append(
                f"AI 拍摄位置 "
                f"{cfg.shot_position_um} μm "
                f"不在搜索范围 "
                f"{cfg.search_start_um}～"
                f"{search_end_um} μm 内"
            )

        if cfg.dl_max_abs_delta_um <= 0:
            errors.append(
                "AI 最大预测偏移必须大于 0"
            )

        elif (
                cfg.dl_max_abs_delta_um
                > cfg.search_span_um
        ):
            warnings.append(
                f"AI 最大预测偏移 "
                f"{cfg.dl_max_abs_delta_um:g} μm "
                f"大于搜索跨度 "
                f"{cfg.search_span_um} μm"
            )

    @staticmethod
    def _validate_template(template_path, errors, warnings):
        """检查搜索模板是否存在、能否读取以及峰值是否过于靠边。"""

        if not template_path:
            errors.append("搜索对焦前必须选择标定模板")
            return

        if not os.path.isfile(template_path):
            errors.append(f"标定模板不存在: {template_path}")
            return

        try:
            from focus_template import FocusTemplate

            template = FocusTemplate.load(template_path)

        except Exception as error:
            errors.append(
                f"标定模板读取失败: {error}"
            )
            return

        point_count = len(template.curve)

        if point_count < 3:
            errors.append(
                f"标定模板只有 {point_count} 个点，无法用于 NCC 搜索"
            )
            return

        peak_index = template.peak_position

        if not 0 <= peak_index < point_count:
            errors.append(
                f"模板峰值序号 {peak_index} 超出曲线范围"
            )
            return

        # 取模板总长度的 5% 作为边界提醒区，同时至少保留 3 个点。
        edge_margin = max(
            3,
            int(point_count * 0.05),
        )

        left_count = peak_index
        right_count = point_count - 1 - peak_index
        edge_distance = min(left_count, right_count)

        if edge_distance < edge_margin:
            warnings.append(
                f"模板峰值位于 {peak_index}/{point_count - 1}，"
                "距离模板边界过近，建议扩大标定范围"
            )
