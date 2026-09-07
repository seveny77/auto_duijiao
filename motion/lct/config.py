# -*- coding: utf-8 -*-
"""M60 单轴运动控制配置。"""

from dataclasses import dataclass
from pathlib import Path

from motion.lct.errors import LctConfigurationError


@dataclass(frozen=True)
class LctMotionConfig:
    """当前自动对焦使用的 M60 静态硬件配置。"""

    m60_dll_path: str
    eni_path: str
    axis_param_path: str
    card_no: int = 0
    axis_no: int = 1
    counts_per_um: int = 100
    positioning_velocity_um_s: float = 100.0
    scan_velocity_um_s: float = 100.0
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

    def __post_init__(self) -> None:
        if self.card_no < 0:
            raise LctConfigurationError(f"M60卡号不能小于0: {self.card_no}")
        if self.axis_no <= 0:
            raise LctConfigurationError(f"M60轴号必须大于0: {self.axis_no}")
        if self.counts_per_um <= 0:
            raise LctConfigurationError(
                f"位置换算比例必须大于0: {self.counts_per_um}"
            )
        if self.positioning_velocity_um_s <= 0:
            raise LctConfigurationError("定位速度必须大于0")
        if self.scan_velocity_um_s <= 0:
            raise LctConfigurationError("连续扫描速度必须大于0")
        if self.position_tolerance_um <= 0:
            raise LctConfigurationError("位置容差必须大于0")
        if self.home_method < 0:
            raise LctConfigurationError("回零模式不能小于0")
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

    def um_to_counts(self, position_um: float) -> int:
        return round(position_um * self.counts_per_um)

    def counts_to_um(self, position_counts: int) -> float:
        return position_counts / self.counts_per_um

    @property
    def positioning_velocity_counts_s(self) -> float:
        return self.positioning_velocity_um_s * self.counts_per_um

    @property
    def position_tolerance_counts(self) -> int:
        return max(1, round(self.position_tolerance_um * self.counts_per_um))

    def validate_files(self) -> None:
        required_files = {
            "M60 DLL": self.m60_dll_path,
            "M60 ENI": self.eni_path,
            "M60轴参数": self.axis_param_path,
        }
        missing_files = [
            f"{description}: {Path(raw_path)}"
            for description, raw_path in required_files.items()
            if not Path(raw_path).is_file()
        ]
        if missing_files:
            raise LctConfigurationError(
                "以下M60运行文件不存在:\n" + "\n".join(missing_files)
            )
