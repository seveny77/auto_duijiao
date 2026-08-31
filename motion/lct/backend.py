# -*- coding: utf-8 -*-
"""M60与E4O4组合而成的正式自动对焦运动后端。"""

import logging
import math
import threading
import time

import perf

from motion.base import MotionBackend
from motion.state import MotionState
from motion.lct.config import LctMotionConfig
from motion.lct.e4o4_api import E4O4Api
from motion.lct.errors import LctSafetyError, LctStateError
from motion.lct.m60_api import M60Api


logger = logging.getLogger(__name__)


class LctMotionBackend(MotionBackend):
    """用M60完成运动、用E4O4按位置输出相机触发。"""

    def __init__(self, config: LctMotionConfig):
        self._config = config
        self._m60 = M60Api(config.m60_dll_path)
        self._e4o4 = E4O4Api(config.e4o4_dll_path)
        self._connected = False
        self._lock = threading.RLock()
        self._stroke_counts = None
        self._homed = False
        self._operation = "disconnected"
        self._operation_cancel_event = threading.Event()

    @property
    def backend_name(self) -> str:
        return "lct"

    def is_connected(self) -> bool:
        return self._connected

    def prepare_new_task(self) -> None:
        """清除上一轮任务遗留的取消请求，不改变设备状态。"""

        with self._lock:
            self._require_connected()
            if self._operation != "idle":
                raise LctStateError(
                    "运动控制器当前忙碌，无法开始新任务: "
                    f"operation={self._operation}"
                )
            self._operation_cancel_event.clear()
            logger.info("新任务运动状态准备完成：已清除历史取消请求")

    def connect(self) -> None:
        """连接两套总线并完成不产生运动的静态初始化。"""

        with self._lock:
            if self._connected:
                return
            self._config.validate_files()

            try:
                m60_init_t0 = time.perf_counter()
                self._m60.load()
                self._m60.open(self._config.card_no)
                self._m60.load_eni(self._config.eni_path)
                self._m60.reset_fpga()
                time.sleep(0.5)
                self._m60.connect_ecat(option=0)
                time.sleep(0.5)
                self._m60.load_axis_params(
                    self._config.axis_param_path
                )
                time.sleep(1.0)
                perf.record(
                    "lct.connect.m60_init_ms",
                    (time.perf_counter() - m60_init_t0) * 1000,
                )

                e4o4_init_t0 = time.perf_counter()
                self._e4o4.load()
                self._e4o4.connect(
                    option=0,
                    net_card_name=self._config.e4o4_net_card,
                )
                time.sleep(0.5)
                self._e4o4.configure_encoder(
                    slave_no=self._config.e4o4_slave_no,
                    encoder_no=self._config.encoder_no,
                    multiplier=self._config.encoder_multiplier,
                    direction=self._config.encoder_direction,
                    enabled=True,
                )
                self._e4o4.configure_trigger_idle(
                    slave_no=self._config.e4o4_slave_no,
                    trigger_no=self._config.trigger_out_no,
                    pulse_width_10ns=(
                        self._config.trigger_pulse_width_10ns
                    ),
                    polarity=self._config.trigger_polarity,
                )
                perf.record(
                    "lct.connect.e4o4_init_ms",
                    (time.perf_counter() - e4o4_init_t0) * 1000,
                )

                status_check_t0 = time.perf_counter()
                status = self._m60.get_axis_status(
                    self._config.axis_no
                )
                if status.offline:
                    raise LctStateError("M60轴掉线，无法建立有效连接")
                self._stroke_counts = self._m60.get_soft_limits(
                    self._config.axis_no
                )
                perf.record(
                    "lct.connect.status_check_ms",
                    (time.perf_counter() - status_check_t0) * 1000,
                )
                self._connected = True
                self._homed = False
                self._operation = "idle"
                self._operation_cancel_event.clear()
                logger.info(
                    "LCT运动后端连接成功: stroke_counts=%s",
                    self._stroke_counts,
                )
            except Exception:
                self._cleanup_partial_connection()
                raise

    def disconnect(self) -> None:
        """停止运动、关闭比较器、去使能并释放两套总线。"""

        with self._lock:
            self._cleanup_partial_connection()
            self._connected = False
            self._stroke_counts = None
            self._homed = False
            self._operation = "disconnected"
            self._operation_cancel_event.set()
            logger.info("LCT运动后端已断开")

    def read_stroke_range(self) -> tuple[int, int]:
        self._require_connected()
        negative, positive = self._stroke_counts
        minimum_um = math.ceil(
            self._config.counts_to_um(negative)
        )
        maximum_um = math.floor(
            self._config.counts_to_um(positive)
        )
        return minimum_um, maximum_um

    def get_state(self) -> MotionState:
        """读取当前轴状态并转换为GUI可直接使用的快照。"""

        with self._lock:
            if not self._connected:
                return MotionState()

            status = self._m60.get_axis_status(
                self._config.axis_no
            )
            emergency = self._m60.get_emergency_stop()
            position_counts = self._m60.get_actual_position(
                self._config.axis_no
            )
            stroke_min, stroke_max = self.read_stroke_range()
            position_um = self._config.counts_to_um(
                position_counts
            )
            ready = (
                self._homed
                and status.servo_enabled
                and not emergency
                and not status.alarm
                and not status.positive_limit
                and not status.negative_limit
                and not status.offline
                and not status.moving
                and self._stroke_counts[0]
                <= position_counts
                <= self._stroke_counts[1]
            )
            message = self._state_message(
                status,
                emergency,
                ready,
            )
            return MotionState(
                connected=True,
                servo_enabled=status.servo_enabled,
                homed=self._homed,
                position_um=position_um,
                stroke_min_um=stroke_min,
                stroke_max_um=stroke_max,
                alarm=status.alarm,
                emergency_stop=emergency,
                positive_limit=status.positive_limit,
                negative_limit=status.negative_limit,
                moving=status.moving,
                offline=status.offline,
                ready_for_autofocus=ready,
                operation=self._operation,
                message=message,
            )

    def is_ready_for_autofocus(self) -> bool:
        return self.get_state().ready_for_autofocus

    def get_homing_parameters(self):
        self._require_connected()
        return self._m60.get_homing_parameters(
            self._config.axis_no
        )

    def clear_alarm(self) -> MotionState:
        """复位驱动器报警，不执行位置运动。"""

        with self._lock:
            self._require_connected()
            status = self._m60.get_axis_status(
                self._config.axis_no
            )
            if status.moving:
                raise LctSafetyError("轴正在运动，不能复位报警")
            if status.servo_enabled:
                self._m60.servo_off(self._config.axis_no)
            self._m60.clear_axis_status(self._config.axis_no)
            time.sleep(0.1)
            state = self.get_state()
            if state.alarm:
                raise LctSafetyError("驱动器报警复位后仍然存在")
            return state

    def servo_on(self) -> MotionState:
        with self._lock:
            self._require_connected()
            self._validate_maintenance_motion_safety()
            self._m60.servo_on(self._config.axis_no)
            return self.get_state()

    def servo_off(self) -> MotionState:
        with self._lock:
            self._require_connected()
            status = self._m60.get_axis_status(
                self._config.axis_no
            )
            if status.moving:
                self._safe_stop_axis()
            self._m60.servo_off(self._config.axis_no)
            return self.get_state()

    def move_to_position(
        self,
        position_um: int,
        timeout_s: float,
        cancel_event=None,
    ) -> MotionState:
        """在不配置E4O4比较器的情况下移动到指定位置并保持。"""

        with self._lock:
            self._require_connected()
            self._raise_if_cancelled(cancel_event)
            if timeout_s <= 0:
                raise ValueError(
                    f"定位超时必须大于0: {timeout_s}"
                )

            target_counts = self._config.um_to_counts(position_um)
            self._validate_target(target_counts)
            self._require_autofocus_ready()
            self._operation = "positioning"
            try:
                actual_counts = self._move_to(
                    target_counts,
                    self._config.positioning_velocity_counts_s,
                    timeout_s,
                    cancel_event,
                )
                logger.info(
                    "最终保持位置定位完成: target=%d, actual=%d",
                    target_counts,
                    actual_counts,
                )
            except Exception:
                self._safe_servo_off()
                raise
            finally:
                self._operation = "idle"
            return self.get_state()

    def cancel_current_motion(self) -> None:
        """请求取消当前动作，并尽快停止轴、关闭伺服。"""

        with self._lock:
            self._operation_cancel_event.set()
            if not self._connected:
                return
            try:
                status = self._m60.get_axis_status(
                    self._config.axis_no
                )
                if status.moving:
                    self._safe_stop_axis()
                if status.servo_enabled:
                    self._safe_servo_off()
            except Exception:
                logger.exception("取消运动时执行安全清理失败")

    def home(
        self,
        cancel_event=None,
        timeout_s: float | None = None,
    ) -> MotionState:
        """使用驱动器现有参数执行一次可取消回零。"""

        with self._lock:
            self._require_connected()
            timeout = (
                self._config.home_timeout_s
                if timeout_s is None
                else float(timeout_s)
            )
            if timeout <= 0:
                raise ValueError(f"回零超时必须大于0: {timeout}")

            self._operation_cancel_event.clear()
            self._homed = False
            self._operation = "homing"
            deadline = time.monotonic() + timeout
            homing_mode_active = False
            homing_started = False
            servo_engaged = False

            try:
                actual_parameters = self.get_homing_parameters()
                expected = (
                    self._config.home_method,
                    self._config.home_offset_counts,
                    self._config.home_speed1_counts_s,
                    self._config.home_speed2_counts_s,
                    self._config.home_acceleration_counts_s2,
                    self._config.home_probe_function,
                )
                actual = (
                    actual_parameters.method,
                    actual_parameters.offset,
                    actual_parameters.speed1,
                    actual_parameters.speed2,
                    actual_parameters.acceleration,
                    actual_parameters.probe_function,
                )
                if actual != expected:
                    logger.warning(
                        "M60回零参数与示例值不同，将以驱动器当前保存值执行: "
                        "expected=%s, actual=%s",
                        expected,
                        actual,
                    )
                logger.info("M60当前回零参数: %s", actual)

                self._validate_maintenance_motion_safety()
                self._raise_if_cancelled_any(cancel_event)
                self._m60.servo_on(self._config.axis_no)
                servo_engaged = True
                self._m60.set_homing_mode(
                    self._config.axis_no,
                    6,
                )
                homing_mode_active = True
                time.sleep(0.05)
                self._raise_if_cancelled_any(cancel_event)
                self._m60.start_homing(self._config.axis_no)
                homing_started = True

                while time.monotonic() < deadline:
                    self._raise_if_cancelled_any(cancel_event)
                    if self._m60.get_emergency_stop():
                        raise LctSafetyError("M60回零期间检测到急停")

                    status = self._m60.get_axis_status(
                        self._config.axis_no
                    )
                    if status.homing_error:
                        raise LctSafetyError("M60驱动器报告回零错误")
                    if status.alarm:
                        raise LctSafetyError("M60回零期间驱动器报警")
                    if status.offline:
                        raise LctSafetyError("M60回零期间轴掉线")

                    command_position = (
                        self._m60.get_command_position(
                            self._config.axis_no
                        )
                    )
                    if (
                        status.homing_completed
                        and status.target_reached
                        and not status.moving
                        and abs(
                            command_position
                            - actual_parameters.offset
                        )
                        <= self._config.home_position_tolerance_counts
                    ):
                        self._m60.set_homing_mode(
                            self._config.axis_no,
                            8,
                        )
                        homing_mode_active = False
                        self._homed = True
                        self._operation = "idle"
                        logger.info(
                            "M60回零成功: command=%.3f, tolerance=%d",
                            command_position,
                            self._config.home_position_tolerance_counts,
                        )
                        return self.get_state()

                    time.sleep(
                        self._config.home_poll_interval_s
                    )

                raise TimeoutError(
                    f"M60回零等待超时: {timeout:.1f}s"
                )

            except Exception:
                self._homed = False
                if homing_started:
                    self._safe_cancel_homing()
                    self._safe_stop_axis()
                if homing_mode_active:
                    self._safe_restore_position_mode()
                if servo_engaged:
                    self._safe_servo_off()
                raise
            finally:
                self._operation = "idle"
                self._operation_cancel_event.clear()

    def linear_fly_scan(
        self,
        start_um: int,
        end_um: int,
        step_um: int,
        timeout_s: float,
        cancel_event=None,
        phase_name="",
        velocity_um_s: float | None = None,
    ) -> int:
        """从start正向飞拍，越过end后再运动配置的末端余量。"""

        with self._lock:
            self._require_connected()
            self._raise_if_cancelled(cancel_event)
            if end_um <= start_um:
                raise ValueError(
                    "当前正式流程只允许正方向线性飞拍: "
                    f"start={start_um}, end={end_um}"
                )
            if step_um <= 0:
                raise ValueError(f"飞拍步距必须大于0: {step_um}")
            span_um = end_um - start_um
            if span_um % step_um != 0:
                raise ValueError(
                    "飞拍区间必须是步距的整数倍: "
                    f"span={span_um}, step={step_um}"
                )
            if timeout_s <= 0:
                raise ValueError(f"飞拍超时必须大于0: {timeout_s}")
            velocity_um_s_by_phase = {
                "calibrate": (
                    self._config
                    .calibrate_scan_velocity_um_s
                ),
                "coarse": (
                    self._config
                    .coarse_scan_velocity_um_s
                ),
                "fine": (
                    self._config
                    .fine_scan_velocity_um_s
                ),
            }

            if velocity_um_s is None:
                velocity_um_s = (
                    velocity_um_s_by_phase.get(
                        phase_name,
                        self._config.scan_velocity_um_s,
                    )
                )
            else:
                velocity_um_s = float(velocity_um_s)
                if velocity_um_s <= 0:
                    raise ValueError(
                        "飞拍速度覆盖值必须大于0: "
                        f"{velocity_um_s}"
                    )

            velocity_counts_s = (
                    velocity_um_s
                    * self._config.counts_per_um
            )
            start_counts = self._config.um_to_counts(start_um)
            logical_end_counts = self._config.um_to_counts(end_um)
            step_counts = self._config.um_to_counts(step_um)
            overrun_counts = self._config.um_to_counts(
                self._config.line_scan_overrun_um
            )
            motion_end_counts = logical_end_counts + overrun_counts
            self._validate_target(start_counts)
            self._validate_target(logical_end_counts)
            self._validate_target(motion_end_counts)
            deadline = time.monotonic() + timeout_s
            detail_t0 = time.perf_counter()
            detail_ct: dict[str, float] = {}
            phase_key = phase_name or "default"

            precheck_t0 = time.perf_counter()
            self._require_autofocus_ready()
            perf.record(
                f"lct.{phase_key}.precheck_ms",
                (time.perf_counter() - precheck_t0) * 1000,
                log=False,
            )
            self._operation = "linear_fly_scan"
            try:
                positioning_t0 = time.perf_counter()
                self._move_to(
                    start_counts,
                    self._config.positioning_velocity_counts_s,
                    self._remaining(deadline),
                    cancel_event,
                )
                detail_ct["positioning_ms"] = (
                    time.perf_counter() - positioning_t0
                ) * 1000

                coordinate_t0 = time.perf_counter()
                m60_start, e4o4_start = self._sample_coordinate_pair()
                detail_ct["coordinate_ms"] = (
                    time.perf_counter() - coordinate_t0
                ) * 1000
            except Exception:
                self._safe_servo_off()
                self._operation = "idle"
                raise
            offset = e4o4_start - m60_start
            trigger_start = start_counts + offset + step_counts
            trigger_end = logical_end_counts + offset
            logger.info(
                "线性飞拍速度："
                "phase=%s，"
                "velocity=%.1f µm/s，"
                "sdk_velocity=%.0f count/s",
                phase_name or "default",
                velocity_um_s,
                velocity_counts_s,
            )
            logger.info(
                "线性飞拍配置：逻辑起点=%d，逻辑终点=%d，"
                "物理终点=%d，触发起点=%d，触发终点=%d，"
                "步距=%d，末端越程=%d count",
                start_counts,
                logical_end_counts,
                motion_end_counts,
                trigger_start,
                trigger_end,
                step_counts,
                overrun_counts,
            )
            move_started = False
            try:
                compare_config_t0 = time.perf_counter()
                line_config = self._configure_line_compare(
                    trigger_start,
                    trigger_end,
                    step_counts,
                )
                detail_ct["compare_config_ms"] = (
                    time.perf_counter() - compare_config_t0
                ) * 1000

                compare_arm_t0 = time.perf_counter()
                self._e4o4.arm_line_compare(
                    self._config.e4o4_slave_no,
                    self._config.line_compare_no,
                )
                detail_ct["compare_arm_ms"] = (
                    time.perf_counter() - compare_arm_t0
                ) * 1000

                scan_motion_t0 = time.perf_counter()
                # 发令与等待分开计时：发令慢是通信问题，
                # 等待慢是运动节拍本身。
                move_issue_t0 = time.perf_counter()
                self._m60.absolute_move(
                    self._config.axis_no,
                    motion_end_counts,
                    velocity_counts_s
                )
                perf.record(
                    f"lct.{phase_key}.scan_move_issue_ms",
                    (time.perf_counter() - move_issue_t0) * 1000,
                )
                move_started = True
                scan_wait_t0 = time.perf_counter()
                m60_final = self._m60.wait_motion_complete(
                    self._config.axis_no,
                    motion_end_counts,
                    self._remaining(deadline),
                    self._config.position_tolerance_counts,
                    cancel_event=cancel_event,
                )
                perf.record(
                    f"lct.{phase_key}.scan_wait_ms",
                    (time.perf_counter() - scan_wait_t0) * 1000,
                )
                move_started = False
                detail_ct["scan_motion_ms"] = (
                    time.perf_counter() - scan_motion_t0
                ) * 1000

                # 触发计数稳定等待：运动到位后 E4O4 计数刷新存在延迟，
                # 轮询 get_trigger_count（纯读，无副作用）直到达到期望值，
                # 上限 200ms，就绪即继续，不再固定睡 50ms
                # （原固定等待见对焦CT拆解报告·发现四）。
                # settle 与读数分开计时：settle 为扣除DLL读数后的纯等待。
                trigger_verify_t0 = time.perf_counter()
                count_reads_ms = 0.0
                settle_deadline = time.monotonic() + 0.2
                while True:
                    count_read_t0 = time.perf_counter()
                    actual_count = self._e4o4.get_trigger_count(
                        self._config.e4o4_slave_no,
                        self._config.trigger_out_no,
                    )
                    count_reads_ms += (
                        time.perf_counter() - count_read_t0
                    ) * 1000
                    if (
                            actual_count
                            >= line_config.expected_trigger_count
                    ):
                        break
                    if time.monotonic() >= settle_deadline:
                        break
                    time.sleep(0.002)
                settle_ms = (
                    time.perf_counter()
                    - trigger_verify_t0
                    - count_reads_ms / 1000.0
                ) * 1000
                trigger_read_t0 = time.perf_counter()
                e4o4_final = self._e4o4.get_encoder_position(
                    self._config.e4o4_slave_no,
                    self._config.encoder_no,
                )
                perf.record(
                    f"lct.{phase_key}.trigger_settle_ms",
                    settle_ms,
                )
                perf.record(
                    f"lct.{phase_key}.trigger_reads_ms",
                    count_reads_ms
                    + (time.perf_counter() - trigger_read_t0) * 1000,
                    log=False,
                )
                detail_ct["trigger_verify_ms"] = (
                    time.perf_counter() - trigger_verify_t0
                ) * 1000
                if actual_count != line_config.expected_trigger_count:
                    # 触发数不符往往意味着缓存与硬件已发散（如触发口
                    # 被外部改写），失效缓存避免下一段fast继续命中、
                    # 形成持续失败循环。
                    self._e4o4.invalidate_comparator_cache()
                    raise LctStateError(
                        "E4O4线性飞拍触发数不符: "
                        f"expected={line_config.expected_trigger_count}, "
                        f"actual={actual_count}, "
                        f"logical_end={logical_end_counts}, "
                        f"motion_end={motion_end_counts}, "
                        f"trigger_end={trigger_end}, "
                        f"m60_final={m60_final}, "
                        f"e4o4_final={e4o4_final}, "
                        f"e4o4_past_trigger="
                        f"{e4o4_final - trigger_end}"
                    )
                logger.info(
                    "线性飞拍完成：实际触发=%d，逻辑终点=%d，"
                    "物理终点=%d，M60实际=%d，E4O4实际=%d，"
                    "越过最后触发点=%d count",
                    actual_count,
                    logical_end_counts,
                    motion_end_counts,
                    m60_final,
                    e4o4_final,
                    e4o4_final - trigger_end,
                )
                return actual_count
            except Exception:
                if move_started:
                    self._safe_stop_axis()
                    move_started = False
                self._safe_servo_off()
                raise
            finally:
                cleanup_t0 = time.perf_counter()
                self._safe_disarm_line()
                detail_ct["cleanup_ms"] = (
                    time.perf_counter() - cleanup_t0
                ) * 1000
                if move_started:
                    self._safe_stop_axis()
                self._operation = "idle"
                logger.info(
                    "LCT CT[%s] | 起点定位 %.1fms | 坐标采样 %.1fms | "
                    "比较器配置 %.1fms | 比较器使能 %.1fms | "
                    "扫描运动 %.1fms | 触发校验 %.1fms | "
                    "比较器清理 %.1fms | 总计 %.1fms",
                    phase_name or "default",
                    detail_ct.get("positioning_ms", 0.0),
                    detail_ct.get("coordinate_ms", 0.0),
                    detail_ct.get("compare_config_ms", 0.0),
                    detail_ct.get("compare_arm_ms", 0.0),
                    detail_ct.get("scan_motion_ms", 0.0),
                    detail_ct.get("trigger_verify_ms", 0.0),
                    detail_ct.get("cleanup_ms", 0.0),
                    (time.perf_counter() - detail_t0) * 1000,
                )
                # detail_ct 整表入全局注册表；cleanup_ms 与搜索级
                # 清理键重名，入表时改名为 cleanup_lct_ms。
                perf.ingest(
                    {
                        (
                            "cleanup_lct_ms"
                            if key == "cleanup_ms"
                            else key
                        ): value
                        for key, value in detail_ct.items()
                    },
                    prefix=f"lct.{phase_key}",
                )

    def capture_at_position(
        self,
        position_um: int,
        timeout_s: float,
        cancel_event=None,
    ) -> int:
        """按配置方向越过目标并触发一次。

        direction=0（正向）：从目标下方准备位置上越（历史行为）；
        direction=1（反向）：从目标上方下越（PreCmp dir=1）。反向时
        若轴已停在目标上方且余量足够（≥准备距离一半，足以加速到
        扫描速度匀速越点），跳过准备定位直接从当前位置起扫。
        """

        with self._lock:
            self._require_connected()
            self._raise_if_cancelled(cancel_event)
            if timeout_s <= 0:
                raise ValueError(f"单点飞拍超时必须大于0: {timeout_s}")

            reverse = self._config.single_capture_direction == 1
            target_counts = self._config.um_to_counts(position_um)
            approach_counts = self._config.um_to_counts(
                self._config.single_capture_approach_um
            )
            exit_counts = self._config.um_to_counts(
                self._config.single_capture_exit_um
            )
            prepare_counts = (
                target_counts + approach_counts
                if reverse
                else target_counts - approach_counts
            )
            finish_counts = (
                target_counts - exit_counts
                if reverse
                else target_counts + exit_counts
            )
            self._validate_target(prepare_counts)
            self._validate_target(target_counts)
            self._validate_target(finish_counts)
            deadline = time.monotonic() + timeout_s
            detail_t0 = time.perf_counter()
            detail_ct: dict[str, float] = {}

            precheck_t0 = time.perf_counter()
            self._require_autofocus_ready()
            perf.record(
                "lct.single.precheck_ms",
                (time.perf_counter() - precheck_t0) * 1000,
                log=False,
            )
            self._operation = "single_capture"
            try:
                positioning_t0 = time.perf_counter()
                current_counts = self._m60.get_actual_position(
                    self._config.axis_no
                )
                # 起始侧余量：已在扫描起始侧且离目标够远时直通起扫，
                # 省掉一次折返准备定位（典型：反向档下精扫末端即起点）。
                # 阈值取10µm而非准备距离的一半：飞拍速度提到匀速只需
                # v²/2a≈4µm（500µm/s、a=31250µm/s²），且流程几何上
                # 余量下限恒为20µm（best_f≤精扫窗上沿），25µm阈值会把
                # 峰值贴窗顶的正常轮次误判回退。
                min_clearance_counts = max(
                    1, self._config.um_to_counts(10)
                )
                clearance_counts = (
                    current_counts - target_counts
                    if reverse
                    else target_counts - current_counts
                )
                if clearance_counts >= min_clearance_counts:
                    detail_ct["positioning_ms"] = (
                        time.perf_counter() - positioning_t0
                    ) * 1000
                    logger.info(
                        "单点飞拍直通：当前位置%d在目标%s侧余量%d count"
                        "（≥%d），跳过准备定位",
                        current_counts,
                        "上" if reverse else "下",
                        clearance_counts,
                        min_clearance_counts,
                    )
                else:
                    logger.info(
                        "单点飞拍回退定位：余量%d count不足（<%d），"
                        "先移动到准备位%d",
                        clearance_counts,
                        min_clearance_counts,
                        prepare_counts,
                    )
                    self._move_to(
                        prepare_counts,
                        self._config.positioning_velocity_counts_s,
                        self._remaining(deadline),
                        cancel_event,
                    )
                    detail_ct["positioning_ms"] = (
                        time.perf_counter() - positioning_t0
                    ) * 1000

                coordinate_t0 = time.perf_counter()
                m60_prepare, e4o4_prepare = self._sample_coordinate_pair()
                detail_ct["coordinate_ms"] = (
                    time.perf_counter() - coordinate_t0
                ) * 1000
            except Exception:
                self._safe_servo_off()
                self._operation = "idle"
                raise
            trigger_position = (
                e4o4_prepare + target_counts - m60_prepare
            )
            move_started = False
            try:
                compare_config_t0 = time.perf_counter()
                pre_config = self._configure_pre_compare(
                    trigger_position,
                    1 if reverse else 0,
                )
                detail_ct["compare_config_ms"] = (
                    time.perf_counter() - compare_config_t0
                ) * 1000

                compare_arm_t0 = time.perf_counter()
                self._e4o4.arm_pre_compare(
                    self._config.e4o4_slave_no,
                    self._config.precompare_no,
                )
                detail_ct["compare_arm_ms"] = (
                    time.perf_counter() - compare_arm_t0
                ) * 1000

                capture_motion_t0 = time.perf_counter()
                # 与 linear_fly_scan 相同：发令 / 等待分开计时。
                move_issue_t0 = time.perf_counter()
                self._m60.absolute_move(
                    self._config.axis_no,
                    finish_counts,
                    self._config.scan_velocity_counts_s,
                )
                perf.record(
                    "lct.single.scan_move_issue_ms",
                    (time.perf_counter() - move_issue_t0) * 1000,
                )
                move_started = True
                scan_wait_t0 = time.perf_counter()
                self._m60.wait_motion_complete(
                    self._config.axis_no,
                    finish_counts,
                    self._remaining(deadline),
                    self._config.position_tolerance_counts,
                    cancel_event=cancel_event,
                )
                perf.record(
                    "lct.single.scan_wait_ms",
                    (time.perf_counter() - scan_wait_t0) * 1000,
                )
                move_started = False
                detail_ct["capture_motion_ms"] = (
                    time.perf_counter() - capture_motion_t0
                ) * 1000

                # 触发计数稳定等待：与 linear_fly_scan 相同的条件轮询
                # （上限 200ms），就绪即继续，不再固定睡 50ms。
                trigger_verify_t0 = time.perf_counter()
                count_reads_ms = 0.0
                settle_deadline = time.monotonic() + 0.2
                while True:
                    count_read_t0 = time.perf_counter()
                    actual_count = self._e4o4.get_trigger_count(
                        self._config.e4o4_slave_no,
                        self._config.trigger_out_no,
                    )
                    count_reads_ms += (
                        time.perf_counter() - count_read_t0
                    ) * 1000
                    if (
                            actual_count
                            >= pre_config.expected_trigger_count
                    ):
                        break
                    if time.monotonic() >= settle_deadline:
                        break
                    time.sleep(0.002)
                perf.record(
                    "lct.single.trigger_settle_ms",
                    (
                        time.perf_counter()
                        - trigger_verify_t0
                        - count_reads_ms / 1000.0
                    ) * 1000,
                )
                perf.record(
                    "lct.single.trigger_reads_ms",
                    count_reads_ms,
                    log=False,
                )
                detail_ct["trigger_verify_ms"] = (
                    time.perf_counter() - trigger_verify_t0
                ) * 1000
                if actual_count != pre_config.expected_trigger_count:
                    self._e4o4.invalidate_comparator_cache()
                    raise LctStateError(
                        "E4O4单点飞拍触发数不符: "
                        f"expected=1, actual={actual_count}"
                    )
                logger.info(
                    "单点飞拍完成：实际触发=%d，越过位置=%d count",
                    actual_count,
                    finish_counts,
                )
                return actual_count
            except Exception:
                if move_started:
                    self._safe_stop_axis()
                    move_started = False
                self._safe_servo_off()
                raise
            finally:
                cleanup_t0 = time.perf_counter()
                self._safe_disarm_pre()
                detail_ct["cleanup_ms"] = (
                    time.perf_counter() - cleanup_t0
                ) * 1000
                if move_started:
                    self._safe_stop_axis()
                self._operation = "idle"
                logger.info(
                    "LCT CT[single] | 方向 %s | 准备定位 %.1fms | "
                    "坐标采样 %.1fms | "
                    "比较器配置 %.1fms | 比较器使能 %.1fms | "
                    "触发运动 %.1fms | 触发校验 %.1fms | "
                    "比较器清理 %.1fms | 总计 %.1fms",
                    "反向" if reverse else "正向",
                    detail_ct.get("positioning_ms", 0.0),
                    detail_ct.get("coordinate_ms", 0.0),
                    detail_ct.get("compare_config_ms", 0.0),
                    detail_ct.get("compare_arm_ms", 0.0),
                    detail_ct.get("capture_motion_ms", 0.0),
                    detail_ct.get("trigger_verify_ms", 0.0),
                    detail_ct.get("cleanup_ms", 0.0),
                    (time.perf_counter() - detail_t0) * 1000,
                )
                # detail_ct 整表入全局注册表（cleanup_ms 改名防撞）。
                perf.ingest(
                    {
                        (
                            "cleanup_lct_ms"
                            if key == "cleanup_ms"
                            else key
                        ): value
                        for key, value in detail_ct.items()
                    },
                    prefix="lct.single",
                )

    def _require_autofocus_ready(self) -> None:
        if not self._homed:
            raise LctSafetyError("本次连接尚未完成回零")
        self._validate_stationary_safety()
        status = self._m60.get_axis_status(
            self._config.axis_no
        )
        if not status.servo_enabled:
            raise LctSafetyError("伺服未使能，不能启动自动对焦")

    def _configure_line_compare(
        self,
        trigger_start: int,
        trigger_end: int,
        step_counts: int,
    ):
        """按配置选择全量或增量路径配置线性比较器。"""

        if self._config.e4o4_incremental_config:
            return self._e4o4.configure_line_compare_fast(
                slave_no=self._config.e4o4_slave_no,
                encoder_no=self._config.encoder_no,
                line_compare_no=self._config.line_compare_no,
                trigger_no=self._config.trigger_out_no,
                start_position=trigger_start,
                end_position=trigger_end,
                interval=step_counts,
                polarity=self._config.trigger_polarity,
            )
        return self._e4o4.configure_line_compare(
            slave_no=self._config.e4o4_slave_no,
            encoder_no=self._config.encoder_no,
            line_compare_no=self._config.line_compare_no,
            trigger_no=self._config.trigger_out_no,
            start_position=trigger_start,
            end_position=trigger_end,
            interval=step_counts,
            polarity=self._config.trigger_polarity,
        )

    def _configure_pre_compare(
        self,
        trigger_position: int,
        direction: int,
    ):
        """按配置选择全量或增量路径配置预设定比较器。"""

        if self._config.e4o4_incremental_config:
            return self._e4o4.configure_pre_compare_fast(
                slave_no=self._config.e4o4_slave_no,
                encoder_no=self._config.encoder_no,
                precompare_no=self._config.precompare_no,
                trigger_no=self._config.trigger_out_no,
                positions=[trigger_position],
                direction=direction,
                polarity=self._config.trigger_polarity,
            )
        return self._e4o4.configure_pre_compare(
            slave_no=self._config.e4o4_slave_no,
            encoder_no=self._config.encoder_no,
            precompare_no=self._config.precompare_no,
            trigger_no=self._config.trigger_out_no,
            positions=[trigger_position],
            direction=direction,
            polarity=self._config.trigger_polarity,
        )

    def _validate_maintenance_motion_safety(self) -> None:
        """手动使能和回零前使用的第一版严格门禁。"""

        if self._m60.get_emergency_stop():
            raise LctSafetyError("M60急停已触发")
        status = self._m60.get_axis_status(
            self._config.axis_no
        )
        problems = []
        if status.alarm:
            problems.append("驱动器报警")
        if status.positive_limit:
            problems.append("正硬限位")
        if status.negative_limit:
            problems.append("负硬限位")
        if status.offline:
            problems.append("轴掉线")
        if status.moving:
            problems.append("轴正在运动")
        if problems:
            raise LctSafetyError(
                "M60当前不允许使能/回零: "
                + "、".join(problems)
            )

    def _move_to(
        self,
        target_counts: int,
        velocity_counts_s: float,
        timeout_s: float,
        cancel_event=None,
    ) -> int:
        try:
            actual = self._m60.get_actual_position(self._config.axis_no)
            if abs(actual - target_counts) <= self._config.position_tolerance_counts:
                return actual
            self._raise_if_cancelled(cancel_event)
            self._m60.absolute_move(
                self._config.axis_no,
                target_counts,
                velocity_counts_s,
            )
            return self._m60.wait_motion_complete(
                self._config.axis_no,
                target_counts,
                timeout_s,
                self._config.position_tolerance_counts,
                cancel_event=cancel_event,
            )
        except Exception:
            self._safe_stop_axis()
            self._safe_servo_off()
            raise

    @staticmethod
    def _event_is_set(cancel_event) -> bool:
        return (
            cancel_event is not None
            and cancel_event.is_set()
        )

    def _raise_if_cancelled(self, cancel_event) -> None:
        if (
            self._event_is_set(cancel_event)
            or self._operation_cancel_event.is_set()
        ):
            raise RuntimeError("用户取消")

    def _raise_if_cancelled_any(self, cancel_event) -> None:
        self._raise_if_cancelled(cancel_event)

    def _sample_coordinate_pair(self) -> tuple[int, int]:
        m60_position = self._m60.get_actual_position(
            self._config.axis_no
        )
        e4o4_position = self._e4o4.get_encoder_position(
            self._config.e4o4_slave_no,
            self._config.encoder_no,
        )
        logger.info(
            "LCT坐标采样: m60=%d, e4o4=%d, offset=%d",
            m60_position,
            e4o4_position,
            e4o4_position - m60_position,
        )
        return m60_position, e4o4_position

    def _validate_stationary_safety(self) -> None:
        if self._m60.get_emergency_stop():
            raise LctSafetyError("M60急停已触发")
        status = self._m60.get_axis_status(self._config.axis_no)
        self._m60._raise_if_unsafe_for_motion(
            self._config.axis_no,
            status,
        )

    def _validate_target(self, target_counts: int) -> None:
        negative, positive = self._stroke_counts
        if not negative <= target_counts <= positive:
            raise LctSafetyError(
                "LCT目标位置超出软件限位: "
                f"target={target_counts}, "
                f"range=[{negative}, {positive}]"
            )

    def _require_connected(self) -> None:
        if not self._connected:
            raise LctStateError("LCT运动后端尚未连接")

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("LCT动作总超时")
        return remaining

    def _safe_disarm_line(self, full: bool = False) -> None:
        """关断线性比较器；增量模式段间保留绑定，full=True完整解绑。"""

        try:
            if (
                self._config.e4o4_incremental_config
                and not full
            ):
                self._e4o4.disarm_line_compare_keep_binding(
                    self._config.e4o4_slave_no,
                    self._config.line_compare_no,
                )
            else:
                self._e4o4.disarm_line_compare(
                    self._config.e4o4_slave_no,
                    self._config.line_compare_no,
                    self._config.trigger_out_no,
                    self._config.trigger_polarity,
                )
        except Exception:
            logger.exception("关闭E4O4线性比较器失败")
            # 清理失败时硬件使能状态未知，失效缓存强制下一段走
            # 全量路径重做SetEnable(0)，避免缓存带着错误armed标志
            # 继续放行fast路径。
            self._e4o4.invalidate_comparator_cache()

    def _safe_disarm_pre(self, full: bool = False) -> None:
        """关断预设定比较器；增量模式段间保留绑定，full=True完整解绑。"""

        try:
            if (
                self._config.e4o4_incremental_config
                and not full
            ):
                self._e4o4.disarm_pre_compare_keep_binding(
                    self._config.e4o4_slave_no,
                    self._config.precompare_no,
                )
            else:
                self._e4o4.disarm_pre_compare(
                    self._config.e4o4_slave_no,
                    self._config.precompare_no,
                    self._config.trigger_out_no,
                    self._config.trigger_polarity,
                )
        except Exception:
            logger.exception("关闭E4O4预设定比较器失败")
            self._e4o4.invalidate_comparator_cache()

    def _safe_stop_axis(self) -> None:
        try:
            self._m60.stop(self._config.axis_no, emergency=True)
        except Exception:
            logger.exception("停止M60轴失败")

    def _safe_cancel_homing(self) -> None:
        try:
            self._m60.cancel_homing(self._config.axis_no)
        except Exception:
            logger.exception("取消M60回零失败")

    def _safe_restore_position_mode(self) -> None:
        try:
            self._m60.set_homing_mode(
                self._config.axis_no,
                8,
            )
        except Exception:
            logger.exception("M60恢复位置模式8失败")

    def _safe_servo_off(self) -> None:
        try:
            self._m60.servo_off(self._config.axis_no)
        except Exception:
            logger.exception("M60安全去使能失败")

    def _state_message(self, status, emergency, ready: bool) -> str:
        if emergency:
            return "急停已触发"
        if status.offline:
            return "轴掉线"
        if status.alarm:
            return "驱动器报警"
        if status.positive_limit:
            return "正硬限位已触发"
        if status.negative_limit:
            return "负硬限位已触发"
        if status.moving:
            return "轴运动中"
        if not self._homed:
            return "已连接，未回零"
        if not status.servo_enabled:
            return "已回零，伺服未使能"
        if ready:
            return "自动对焦已就绪"
        return "已连接"

    def _cleanup_partial_connection(self) -> None:
        if self._e4o4.is_connected:
            # 断开连接走完整解绑（增量模式也不保留绑定）。
            self._safe_disarm_line(full=True)
            self._safe_disarm_pre(full=True)
            self._e4o4.close()

        if self._m60.ecat_connected:
            try:
                status = self._m60.get_axis_status(self._config.axis_no)
                if status.moving:
                    self._safe_stop_axis()
                self._m60.servo_off(self._config.axis_no)
            except Exception:
                logger.exception("清理M60运动状态失败")
        self._m60.close()


# ── CT 类级插桩 ──
# 给 LctMotionBackend 全部公开方法（connect/get_state/move_to_position/
# home/linear_fly_scan/capture_at_position/cancel_current_motion 等）套
# 计时壳：每次调用进 perf 注册表，默认静默，单次 ≥200ms 打 [CT][慢]。
# 方法内部的细分耗时（飞拍各段）由函数体内的 perf.record 单独记录，
# 两者键名不同、互不覆盖。
perf.instrument_class(LctMotionBackend, "lct", slow_ms=200.0)
