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
    e4o4_param_path: str

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
            "E4O4参数": self.e4o4_param_path,
        }

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