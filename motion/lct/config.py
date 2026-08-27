# -*- coding: utf-8 -*-
"""M60和E4O4运动控制配置。"""

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Optional

from motion.lct.errors import LctConfigurationError


class ScanDirection(IntEnum):
    """飞拍运动方向。

    数值与E4O4预设定比较器的方向参数保持一致。
    """

    POSITIVE = 0
    NEGATIVE = 1


@dataclass(frozen=True)
class LctMotionConfig:
    """M60和E4O4的静态硬件配置。

    路径参数由工控机本地配置提供，不在代码中写死。
    """

    m60_dll_path: str
    e4o4_dll_path: str
    eni_path: str
    axis_param_path: str
    e4o4_param_path: Optional[str] = None

    card_no: int = 0
    axis_no: int = 1

    e4o4_slave_no: int = 1
    encoder_no: int = 0
    trigger_out_no: int = 0
    line_compare_no: int = 0
    precompare_no: int = 0

    counts_per_um: int = 100

    encoder_multiplier: int = 4
    encoder_direction: int = 1

    trigger_pulse_width_10ns: int = 2000
    trigger_polarity: int = 0

    positioning_velocity_um_s: float = 100.0
    scan_velocity_um_s: float = 100.0

    calibrate_scan_velocity_um_s: float = 100.0
    coarse_scan_velocity_um_s: float = 1000.0
    fine_scan_velocity_um_s: float = 50.0

    line_scan_overrun_um: float = 20.0
    single_capture_approach_um: int = 50
    single_capture_exit_um: int = 50
    position_tolerance_um: float = 1.0

    home_method: int = 33
    home_offset_counts: int = 0
    home_speed1_counts_s: int = 10000
    home_speed2_counts_s: int = 2000
    home_acceleration_counts_s2: int = 100000
    home_probe_function: int = 0
    home_position_tolerance_counts: int = 50
    home_timeout_s: float = 900.0
    home_poll_interval_s: float = 0.05



    e4o4_net_card: Optional[str] = None

    def __post_init__(self) -> None:
        """检查不依赖真实硬件的基础参数。"""

        if self.card_no < 0:
            raise LctConfigurationError(
                f"M60卡号不能小于0: {self.card_no}"
            )

        if self.axis_no <= 0:
            raise LctConfigurationError(
                f"M60轴号必须大于0: {self.axis_no}"
            )

        if self.e4o4_slave_no <= 0:
            raise LctConfigurationError(
                "E4O4从站号必须从1开始: "
                f"{self.e4o4_slave_no}"
            )

        if self.encoder_no < 0:
            raise LctConfigurationError(
                f"E4O4编码器通道不能小于0: {self.encoder_no}"
            )

        if self.trigger_out_no < 0:
            raise LctConfigurationError(
                f"E4O4触发通道不能小于0: {self.trigger_out_no}"
            )

        if self.counts_per_um <= 0:
            raise LctConfigurationError(
                f"位置换算比例必须大于0: {self.counts_per_um}"
            )

        if self.encoder_multiplier not in (1, 2, 4):
            raise LctConfigurationError(
                "E4O4编码器倍频只能是1、2或4: "
                f"{self.encoder_multiplier}"
            )

        if self.encoder_direction not in (0, 1):
            raise LctConfigurationError(
                "E4O4编码器方向只能是0或1: "
                f"{self.encoder_direction}"
            )

        if self.trigger_pulse_width_10ns <= 0:
            raise LctConfigurationError(
                "E4O4触发脉宽必须大于0: "
                f"{self.trigger_pulse_width_10ns}"
            )

        if self.trigger_polarity not in (0, 1):
            raise LctConfigurationError(
                "E4O4触发极性只能是0或1: "
                f"{self.trigger_polarity}"
            )

        if self.positioning_velocity_um_s <= 0:
            raise LctConfigurationError(
                "定位速度必须大于0: "
                f"{self.positioning_velocity_um_s}"
            )
        phase_velocities = {
            "标定飞拍速度": (
                self.calibrate_scan_velocity_um_s
            ),
            "粗扫飞拍速度": (
                self.coarse_scan_velocity_um_s
            ),
            "精扫飞拍速度": (
                self.fine_scan_velocity_um_s
            ),
        }

        for name, value in phase_velocities.items():
            if value <= 0:
                raise LctConfigurationError(
                    f"{name}必须大于0: {value}"
                )

        if self.scan_velocity_um_s <= 0:
            raise LctConfigurationError(
                f"飞拍速度必须大于0: {self.scan_velocity_um_s}"
            )
        if self.line_scan_overrun_um <= 0:
            raise LctConfigurationError(
                "线性飞拍末端越程必须大于0: "
                f"{self.line_scan_overrun_um}"
            )
        if self.single_capture_approach_um <= 0:
            raise LctConfigurationError(
                "单点飞拍准备距离必须大于0: "
                f"{self.single_capture_approach_um}"
            )
        if self.single_capture_exit_um <= 0:
            raise LctConfigurationError(
                "单点飞拍越过距离必须大于0: "
                f"{self.single_capture_exit_um}"
            )
        if self.position_tolerance_um <= 0:
            raise LctConfigurationError(
                "位置容差必须大于0: "
                f"{self.position_tolerance_um}"
            )
        if self.home_method < 0:
            raise LctConfigurationError(
                f"回零模式不能小于0: {self.home_method}"
            )
        if self.home_speed1_counts_s <= 0:
            raise LctConfigurationError("回零高速必须大于0")
        if self.home_speed2_counts_s <= 0:
            raise LctConfigurationError("回零低速必须大于0")
        if self.home_acceleration_counts_s2 <= 0:
            raise LctConfigurationError("回零加速度必须大于0")
        if self.home_position_tolerance_counts < 0:
            raise LctConfigurationError("回零位置容差不能小于0")
        if self.home_timeout_s <= 0:
            raise LctConfigurationError("回零超时必须大于0")
        if self.home_poll_interval_s <= 0:
            raise LctConfigurationError("回零轮询周期必须大于0")

    @property
    def trigger_pulse_width_us(self) -> float:
        """返回以微秒为单位的触发脉宽。"""

        return self.trigger_pulse_width_10ns / 100.0

    def um_to_counts(self, position_um: float) -> int:
        """把微米位置转换成M60/E4O4计数。"""

        return round(
            position_um * self.counts_per_um
        )

    def counts_to_um(self, position_counts: int) -> float:
        """把M60/E4O4计数转换成微米。"""

        return (
            position_counts
            / self.counts_per_um
        )

    @property
    def positioning_velocity_counts_s(self) -> float:
        return self.positioning_velocity_um_s * self.counts_per_um

    @property
    def scan_velocity_counts_s(self) -> float:
        return self.scan_velocity_um_s * self.counts_per_um

    @property
    def position_tolerance_counts(self) -> int:
        return max(1, round(self.position_tolerance_um * self.counts_per_um))

    def validate_files(self) -> None:
        """确认所有SDK和参数文件都存在。

        这个方法不会在创建配置对象时自动调用，因而笔记本上即使
        没有工控机SDK，也可以导入、测试和编辑程序。
        """

        required_files = {
            "M60 DLL": self.m60_dll_path,
            "E4O4 DLL": self.e4o4_dll_path,
            "M60 ENI": self.eni_path,
            "M60轴参数": self.axis_param_path,
        }

        if self.e4o4_param_path:
            required_files["E4O4参数"] = self.e4o4_param_path

        missing_files = []

        for description, raw_path in required_files.items():
            path = Path(raw_path)

            if not path.is_file():
                missing_files.append(
                    f"{description}: {path}"
                )

        if missing_files:
            detail = "\n".join(missing_files)

            raise LctConfigurationError(
                "以下LCT运行文件不存在:\n"
                f"{detail}"
            )
