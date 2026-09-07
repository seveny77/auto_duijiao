"""连续自动对焦任务的启动、校验与后台提交。"""

import logging


logger = logging.getLogger(__name__)


class FocusRunService:
    def __init__(
        self, config_service, controller, focus_task_service, live_view_service,
        result_presenter, stroke_range_fn, motion_backend_fn, motion_state_fn,
        camera_fn, camera_roi_applied_fn, camera_roi_current_fn, confirm_fn,
        status_fn,
    ):
        self._config_service = config_service
        self._controller = controller
        self._focus_task_service = focus_task_service
        self._live_view_service = live_view_service
        self._result_presenter = result_presenter
        self._stroke_range_fn = stroke_range_fn
        self._motion_backend_fn = motion_backend_fn
        self._motion_state_fn = motion_state_fn
        self._camera_fn = camera_fn
        self._camera_roi_applied_fn = camera_roi_applied_fn
        self._camera_roi_current_fn = camera_roi_current_fn
        self._confirm_fn = confirm_fn
        self._status_fn = status_fn

    def start(self):
        if self._live_view_service.is_active or self._live_view_service.is_running:
            if not self._live_view_service.stop():
                self._status_fn("请等待实时预览停止后再启动任务")
                return False

        cfg = self._config_service.build_focus_config()
        if cfg.mode == "real":
            cfg.motion_backend = self._motion_backend_fn()
            cfg.camera = self._camera_fn()
        errors, warnings = self._validate_config(cfg)
        for message in warnings:
            logger.warning("参数提醒: %s", message)
        if errors:
            for message in errors:
                logger.error("参数错误: %s", message)
            self._status_fn(errors[0])
            return False

        if cfg.mode == "real" and not cfg.yes:
            if not self._confirm_fn(
                "即将执行连续自动对焦。\n\n"
                "Z 轴将从扫描起点单向运动到终点，再自动回到起点。\n"
                "请确认机械区域安全后继续。"
            ):
                self._status_fn("用户取消执行")
                return False
            cfg.yes = True

        self._result_presenter.begin_task()
        cfg.cancel_event = self._controller.new_cancel_event()
        self._controller.set_state(self._controller.STATE_RUNNING)
        if self._focus_task_service.start(cfg):
            return True
        self._controller.set_state(self._controller.STATE_ERROR)
        self._status_fn("后台任务启动失败")
        return False

    def _validate_config(self, cfg):
        errors, warnings = [], []
        start = int(cfg.search_start_um)
        end = start + int(cfg.search_span_um)
        if cfg.search_span_um <= 0:
            errors.append("扫描跨度必须大于 0")
        if cfg.continuous_scan_velocity_um_s <= 0:
            errors.append("连续运动速度必须大于 0")
        if cfg.continuous_capture_fps <= 0:
            errors.append("连续采集帧率必须大于 0")
        if cfg.continuous_first_frame_timeout_s <= 0:
            errors.append("首帧等待超时必须大于 0")

        stroke_range = self._stroke_range_fn()
        if cfg.mode == "real":
            motion = cfg.motion_backend
            if motion is None:
                errors.append("请先连接M60运动控制器")
            else:
                try:
                    state = self._motion_state_fn()
                except Exception as error:
                    errors.append(f"读取运动控制器状态失败: {error}")
                else:
                    if not state.connected:
                        errors.append("运动控制器已断开，请重新连接")
                    elif not state.homed:
                        errors.append("本次连接尚未回原点，请先点击“回原点”")
                    elif not state.servo_enabled:
                        errors.append("伺服未使能，请先手动使能")
                    elif state.alarm or state.emergency_stop or state.offline:
                        errors.append(state.message or "运动控制器尚未就绪")
                    elif not state.ready_for_autofocus:
                        errors.append(state.message or "运动控制器尚未就绪")
            camera = cfg.camera
            if camera is None or not camera.is_connected:
                errors.append("请先连接相机")
            elif not self._camera_roi_applied_fn():
                errors.append("请先点击“应用相机 ROI”，再开始真实对焦")
            elif not self._camera_roi_current_fn(
                cfg.work_roi_width_px, cfg.work_roi_height_px, cfg.camera_decimation,
            ):
                errors.append("硬件 ROI 参数已改变，请重新点击“应用相机 ROI”")

        if cfg.mode == "real" and stroke_range is not None:
            minimum, maximum = stroke_range
            if start < minimum:
                errors.append(f"扫描起点 {start} µm 小于轴卡软件限位 {minimum} µm")
            if end > maximum:
                errors.append(f"扫描终点 {end} µm 大于轴卡软件限位 {maximum} µm")
        elif cfg.mode == "real":
            warnings.append("尚未读取轴卡行程，本次无法提前校验扫描范围")
        return errors, warnings
