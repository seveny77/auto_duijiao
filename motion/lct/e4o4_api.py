# -*- coding: utf-8 -*-
"""凌臣E4O4飞拍模块SDK的只读Python封装。"""

import ctypes
from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Optional

from motion.lct.errors import (
    LctLibraryLoadError,
    LctSdkCallError,
    LctStateError,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class E4O4SlaveResource:
    """E4O4从站公开的硬件资源数量。"""

    di_num: int
    do_num: int
    ai_num: int
    ao_num: int
    latch_num: int
    encoder_num: int
    trigger_num: int


@dataclass(frozen=True)
class E4O4EncoderConfig:
    """E4O4编码器通道的当前只读配置。"""

    multiplier: int
    direction: int
    enabled: bool
    filter_count: int


@dataclass(frozen=True)
class E4O4TriggerConfig:
    """E4O4触发输出通道的当前只读配置。"""

    output_mode: int
    trigger_mode: int
    pulse_width_10ns: int
    line_compare_mask: int
    precompare_mask: int
    polarity: int
    trigger_count: int

    @property
    def pulse_width_us(self) -> float:
        """把厂家10 ns单位转换成微秒。"""

        return self.pulse_width_10ns / 100.0


@dataclass(frozen=True)
class E4O4LineCompareConfig:
    """线性比较器当前配置及预计触发数量。"""

    encoder_no: int
    line_compare_no: int
    trigger_no: int
    start_position: int
    end_position: int
    interval: int
    expected_trigger_count: int


@dataclass(frozen=True)
class E4O4PreCompareConfig:
    """预设定比较器当前配置。"""

    encoder_no: int
    precompare_no: int
    trigger_no: int
    direction: int
    positions: tuple[int, ...]

    @property
    def expected_trigger_count(self) -> int:
        return len(self.positions)


class E4O4Api:
    """MiniEcatLib.dll的E4O4只读底层封装。

    本阶段故意不封装设置编码器位置、手动输出、比较器配置和
    比较器使能函数，防止诊断脚本意外产生相机触发。
    """

    def __init__(self, dll_path: str):
        self._dll_path = Path(dll_path)
        self._dll = None
        self._dll_directory_handle = None
        self._connected = False
        self._slave_count = 0

    @property
    def dll_path(self) -> Path:
        return self._dll_path

    @property
    def is_loaded(self) -> bool:
        return self._dll is not None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def slave_count(self) -> int:
        return self._slave_count

    def load(self) -> None:
        """加载64位MiniEcatLib.dll并绑定函数签名。"""

        if self._dll is not None:
            return
        if not self._dll_path.is_file():
            raise LctLibraryLoadError(
                f"E4O4 DLL不存在: {self._dll_path}"
            )

        try:
            if os.name == "nt" and hasattr(os, "add_dll_directory"):
                self._dll_directory_handle = os.add_dll_directory(
                    str(self._dll_path.parent)
                )
            self._dll = ctypes.CDLL(str(self._dll_path))
            self._bind_functions()
        except (OSError, AttributeError) as error:
            self._dll = None
            python_bits = ctypes.sizeof(ctypes.c_void_p) * 8
            raise LctLibraryLoadError(
                "加载E4O4 DLL失败: "
                f"path={self._dll_path}, Python={python_bits}位, "
                f"原因={error}"
            ) from error

        logger.info("E4O4 DLL加载成功: %s", self._dll_path)

    def connect(
        self,
        option: int = 0,
        net_card_name: Optional[str] = None,
    ) -> int:
        """连接E4O4总线，不修改参数、不输出触发脉冲。"""

        self._require_loaded()
        if self._connected:
            return self._slave_count
        if option not in (0, 1):
            raise ValueError(f"E4O4连接选项只能是0或1: {option}")

        if net_card_name:
            self._dll.Mb_SelectNetCard(
                self._encode_sdk_text(net_card_name)
            )

        slave_count = ctypes.c_int()
        result = self._dll.Mb_InitEcat(
            ctypes.byref(slave_count),
            ctypes.c_int(option),
        )
        self._check_result("Mb_InitEcat", result)

        if slave_count.value <= 0:
            self._dll.Mb_CloseEcat()
            raise LctStateError(
                "E4O4总线未发现从站: "
                f"result={int(result)}, slave_count={slave_count.value}"
            )

        self._connected = True
        self._slave_count = int(slave_count.value)
        logger.info(
            "E4O4总线连接成功: slave_count=%d, option=%d",
            self._slave_count,
            option,
        )
        return self._slave_count

    def close(self) -> None:
        """关闭E4O4总线连接。"""

        if self._dll is None or not self._connected:
            return
        self._dll.Mb_CloseEcat()
        self._connected = False
        self._slave_count = 0
        logger.info("E4O4总线已关闭")

    def get_connect_status(self, slave_no: int) -> int:
        """读取指定E4O4从站的厂家原始连接状态。"""

        self._require_slave(slave_no)
        status = ctypes.c_int()
        result = self._dll.Mb_GetConnectStatus(
            ctypes.c_int(slave_no),
            ctypes.byref(status),
        )
        self._check_result("Mb_GetConnectStatus", result)
        return int(status.value)

    def get_slave_name(self, slave_no: int) -> str:
        """读取指定从站名称。"""

        self._require_slave(slave_no)
        raw_pointer = self._dll.Mb_GetSlaveName(ctypes.c_int(slave_no))
        if not raw_pointer:
            raise LctStateError(
                f"E4O4从站名称指针为空: slave={slave_no}"
            )
        raw_name = ctypes.string_at(raw_pointer)
        return self._decode_sdk_text(raw_name)

    def get_version(self, slave_no: int) -> tuple[int, int]:
        """读取从站硬件版本和模块版本原始字节。"""

        self._require_slave(slave_no)
        hardware = ctypes.c_ubyte()
        module = ctypes.c_ubyte()
        result = self._dll.Mb_GetVersion(
            ctypes.c_int(slave_no),
            ctypes.byref(hardware),
            ctypes.byref(module),
        )
        self._check_result("Mb_GetVersion", result)
        return int(hardware.value), int(module.value)

    def get_slave_resource(self, slave_no: int) -> E4O4SlaveResource:
        """读取指定从站的DI/DO/编码器/触发等资源数量。"""

        self._require_slave(slave_no)
        values = [ctypes.c_int() for _ in range(7)]
        result = self._dll.Mb_GetSlaveResource(
            ctypes.c_int(slave_no),
            *(ctypes.byref(value) for value in values),
        )
        self._check_result("Mb_GetSlaveResource", result)
        raw = [int(value.value) for value in values]
        return E4O4SlaveResource(*raw)

    def get_encoder_position(self, slave_no: int, encoder_no: int) -> int:
        """读取E4O4编码器位置原始计数。"""

        self._require_channel(slave_no, encoder_no, "编码器")
        position = ctypes.c_int()
        result = self._dll.Mb_E4O4Encoder_GetEncoderData(
            ctypes.c_int(slave_no),
            ctypes.c_int(encoder_no),
            ctypes.byref(position),
        )
        self._check_result("Mb_E4O4Encoder_GetEncoderData", result)
        return int(position.value)

    def get_encoder_config(
        self,
        slave_no: int,
        encoder_no: int,
    ) -> E4O4EncoderConfig:
        """读取编码器倍频、方向、使能和滤波计数。"""

        self._require_channel(slave_no, encoder_no, "编码器")
        multiplier = ctypes.c_int()
        direction = ctypes.c_int()
        enabled = ctypes.c_int()
        result = self._dll.Mb_E4O4Encoder_GetParam(
            ctypes.c_int(slave_no),
            ctypes.c_int(encoder_no),
            ctypes.byref(multiplier),
            ctypes.byref(direction),
            ctypes.byref(enabled),
        )
        self._check_result("Mb_E4O4Encoder_GetParam", result)

        filter_count = ctypes.c_int()
        result = self._dll.Mb_E4O4Encoder_GetFilterCount(
            ctypes.c_int(slave_no),
            ctypes.byref(filter_count),
        )
        self._check_result("Mb_E4O4Encoder_GetFilterCount", result)
        return E4O4EncoderConfig(
            multiplier=int(multiplier.value),
            direction=int(direction.value),
            enabled=bool(enabled.value),
            filter_count=int(filter_count.value),
        )

    def configure_encoder(
        self,
        slave_no: int,
        encoder_no: int,
        multiplier: int,
        direction: int,
        enabled: bool = True,
    ) -> E4O4EncoderConfig:
        """设置编码器倍频、方向和使能，然后回读验证。

        本方法不设置编码器当前位置，因此不会改变位置零点。
        """

        self._require_channel(slave_no, encoder_no, "编码器")
        if multiplier not in (1, 2, 4):
            raise ValueError(
                f"E4O4编码器倍频只能是1、2或4: {multiplier}"
            )
        if direction not in (0, 1):
            raise ValueError(
                f"E4O4编码器方向只能是0或1: {direction}"
            )

        result = self._dll.Mb_E4O4Encoder_Initial(
            ctypes.c_int(slave_no),
            ctypes.c_int(encoder_no),
            ctypes.c_int(multiplier),
            ctypes.c_int(direction),
            ctypes.c_int(1 if enabled else 0),
        )
        self._check_result("Mb_E4O4Encoder_Initial", result)
        return self.get_encoder_config(slave_no, encoder_no)

    def configure_trigger_idle(
        self,
        slave_no: int,
        trigger_no: int,
        pulse_width_10ns: int,
        polarity: int = 0,
    ) -> E4O4TriggerConfig:
        """配置触发口的安全空闲状态并回读。

        调用顺序先解除线性和预设定比较器绑定，再设置脉冲输出、
        脉冲触发模式和脉宽。本方法不调用任何手动触发函数。
        """

        self._require_channel(slave_no, trigger_no, "触发输出")
        if not 100 <= pulse_width_10ns <= 9_999_999:
            raise ValueError(
                "E4O4触发脉宽超出厂家范围100～9999999: "
                f"{pulse_width_10ns}"
            )
        if polarity not in (0, 1):
            raise ValueError(
                f"E4O4触发极性只能是0或1: {polarity}"
            )

        result = self._dll.Mb_E4O4TrigOut_BandingCompare(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.c_uint(0),
            ctypes.c_uint(0),
            ctypes.c_uint(polarity),
        )
        self._check_result("Mb_E4O4TrigOut_BandingCompare", result)

        result = self._dll.Mb_E4O4TrigOut_SetOutMode(
            ctypes.c_int(slave_no),
            ctypes.c_int(1),
        )
        self._check_result("Mb_E4O4TrigOut_SetOutMode", result)

        result = self._dll.Mb_E4O4TrigOut_SetTrigMode(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.c_int(0),
        )
        self._check_result("Mb_E4O4TrigOut_SetTrigMode", result)

        result = self._dll.Mb_E4O4TrigOut_SetPulseWidth(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.c_int(pulse_width_10ns),
        )
        self._check_result("Mb_E4O4TrigOut_SetPulseWidth", result)

        return self.get_trigger_config(slave_no, trigger_no)

    def get_trigger_config(
        self,
        slave_no: int,
        trigger_no: int,
    ) -> E4O4TriggerConfig:
        """读取触发输出模式、脉宽、绑定和累计次数。"""

        self._require_channel(slave_no, trigger_no, "触发输出")
        output_mode = ctypes.c_int()
        trigger_mode = ctypes.c_int()
        pulse_width = ctypes.c_int()
        line_mask = ctypes.c_uint()
        pre_mask = ctypes.c_uint()
        polarity = ctypes.c_int()
        trigger_count = ctypes.c_int()

        calls = (
            (
                "Mb_E4O4TrigOut_GetOutMode",
                self._dll.Mb_E4O4TrigOut_GetOutMode(
                    ctypes.c_int(slave_no), ctypes.byref(output_mode)
                ),
            ),
            (
                "Mb_E4O4TrigOut_GetTrigMode",
                self._dll.Mb_E4O4TrigOut_GetTrigMode(
                    ctypes.c_int(slave_no),
                    ctypes.c_int(trigger_no),
                    ctypes.byref(trigger_mode),
                ),
            ),
            (
                "Mb_E4O4TrigOut_GetPulseWidth",
                self._dll.Mb_E4O4TrigOut_GetPulseWidth(
                    ctypes.c_int(slave_no),
                    ctypes.c_int(trigger_no),
                    ctypes.byref(pulse_width),
                ),
            ),
            (
                "Mb_E4O4TrigOut_GetBanding",
                self._dll.Mb_E4O4TrigOut_GetBanding(
                    ctypes.c_int(slave_no),
                    ctypes.c_int(trigger_no),
                    ctypes.byref(line_mask),
                    ctypes.byref(pre_mask),
                    ctypes.byref(polarity),
                ),
            ),
            (
                "Mb_E4O4TrigOut_GetCounter",
                self._dll.Mb_E4O4TrigOut_GetCounter(
                    ctypes.c_int(slave_no),
                    ctypes.c_int(trigger_no),
                    ctypes.byref(trigger_count),
                ),
            ),
        )
        for operation, result in calls:
            self._check_result(operation, result)

        return E4O4TriggerConfig(
            output_mode=int(output_mode.value),
            trigger_mode=int(trigger_mode.value),
            pulse_width_10ns=int(pulse_width.value),
            line_compare_mask=int(line_mask.value),
            precompare_mask=int(pre_mask.value),
            polarity=int(polarity.value),
            trigger_count=int(trigger_count.value),
        )

    def get_trigger_count(self, slave_no: int, trigger_no: int) -> int:
        """读取触发输出通道的累计触发次数。"""

        self._require_channel(slave_no, trigger_no, "触发输出")
        trigger_count = ctypes.c_int()
        result = self._dll.Mb_E4O4TrigOut_GetCounter(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.byref(trigger_count),
        )
        self._check_result("Mb_E4O4TrigOut_GetCounter", result)
        return int(trigger_count.value)

    def reset_trigger_count(self, slave_no: int, trigger_no: int) -> None:
        """清零指定触发输出通道的累计触发次数。"""

        self._require_channel(slave_no, trigger_no, "触发输出")
        result = self._dll.Mb_E4O4TrigOut_ResetCounter(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
        )
        self._check_result("Mb_E4O4TrigOut_ResetCounter", result)

    def configure_line_compare(
        self,
        slave_no: int,
        encoder_no: int,
        line_compare_no: int,
        trigger_no: int,
        start_position: int,
        end_position: int,
        interval: int,
        polarity: int = 0,
    ) -> E4O4LineCompareConfig:
        """配置并回读线性比较器，但保持比较器关闭。"""

        self._require_channel(slave_no, encoder_no, "编码器")
        self._require_channel(slave_no, line_compare_no, "线性比较器")
        self._require_channel(slave_no, trigger_no, "触发输出")
        if start_position == end_position:
            raise ValueError("线性比较起点和终点不能相同")
        if interval <= 0:
            raise ValueError(f"线性比较间隔必须大于0: {interval}")
        if polarity not in (0, 1):
            raise ValueError(f"E4O4触发极性只能是0或1: {polarity}")

        distance = abs(end_position - start_position)
        if distance % interval != 0:
            raise ValueError(
                "线性比较区间必须是步距的整数倍: "
                f"distance={distance}, interval={interval}"
            )
        expected_count = distance // interval + 1

        self.disarm_line_compare(
            slave_no=slave_no,
            line_compare_no=line_compare_no,
            trigger_no=trigger_no,
            polarity=polarity,
        )
        self.reset_trigger_count(slave_no, trigger_no)

        result = self._dll.Mb_E4O4LineCmp_BingdingEncoder(
            ctypes.c_int(slave_no),
            ctypes.c_int(encoder_no),
            ctypes.c_int(line_compare_no),
        )
        self._check_result("Mb_E4O4LineCmp_BingdingEncoder", result)

        result = self._dll.Mb_E4O4LineCmp_SetTriggerData(
            ctypes.c_int(slave_no),
            ctypes.c_int(line_compare_no),
            ctypes.c_int(start_position),
            ctypes.c_int(end_position),
            ctypes.c_int(interval),
        )
        self._check_result("Mb_E4O4LineCmp_SetTriggerData", result)

        line_mask = 1 << line_compare_no
        result = self._dll.Mb_E4O4TrigOut_BandingCompare(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.c_uint(line_mask),
            ctypes.c_uint(0),
            ctypes.c_uint(polarity),
        )
        self._check_result("Mb_E4O4TrigOut_BandingCompare", result)

        actual_encoder = ctypes.c_int()
        result = self._dll.Mb_E4O4LineCmp_GetBingdingEncoder(
            ctypes.c_int(slave_no),
            ctypes.c_int(line_compare_no),
            ctypes.byref(actual_encoder),
        )
        self._check_result("Mb_E4O4LineCmp_GetBingdingEncoder", result)

        actual_start = ctypes.c_int()
        actual_end = ctypes.c_int()
        actual_interval = ctypes.c_int()
        result = self._dll.Mb_E4O4LineCmp_GetTriggerData(
            ctypes.c_int(slave_no),
            ctypes.c_int(line_compare_no),
            ctypes.byref(actual_start),
            ctypes.byref(actual_end),
            ctypes.byref(actual_interval),
        )
        self._check_result("Mb_E4O4LineCmp_GetTriggerData", result)

        trigger_config = self.get_trigger_config(slave_no, trigger_no)
        actual = (
            int(actual_encoder.value),
            int(actual_start.value),
            int(actual_end.value),
            int(actual_interval.value),
            trigger_config.line_compare_mask,
            trigger_config.precompare_mask,
            trigger_config.polarity,
        )
        expected = (
            encoder_no,
            start_position,
            end_position,
            interval,
            line_mask,
            0,
            polarity,
        )
        if actual != expected:
            raise LctStateError(
                "E4O4线性比较器配置回读不一致: "
                f"expected={expected}, actual={actual}"
            )

        return E4O4LineCompareConfig(
            encoder_no=encoder_no,
            line_compare_no=line_compare_no,
            trigger_no=trigger_no,
            start_position=start_position,
            end_position=end_position,
            interval=interval,
            expected_trigger_count=expected_count,
        )

    def arm_line_compare(self, slave_no: int, line_compare_no: int) -> None:
        """使能指定线性比较器。"""

        self._require_channel(slave_no, line_compare_no, "线性比较器")
        result = self._dll.Mb_E4O4LineCmp_SetEnable(
            ctypes.c_int(slave_no),
            ctypes.c_int(line_compare_no),
            ctypes.c_int(1),
        )
        self._check_result("Mb_E4O4LineCmp_SetEnable", result)
        logger.info(
            "E4O4线性比较器已使能: slave=%d, comparator=%d",
            slave_no,
            line_compare_no,
        )

    def disarm_line_compare(
        self,
        slave_no: int,
        line_compare_no: int,
        trigger_no: int,
        polarity: int = 0,
    ) -> None:
        """关闭线性比较器，并解除触发输出绑定。"""

        self._require_channel(slave_no, line_compare_no, "线性比较器")
        self._require_channel(slave_no, trigger_no, "触发输出")
        result = self._dll.Mb_E4O4LineCmp_SetEnable(
            ctypes.c_int(slave_no),
            ctypes.c_int(line_compare_no),
            ctypes.c_int(0),
        )
        self._check_result("Mb_E4O4LineCmp_SetEnable", result)
        result = self._dll.Mb_E4O4TrigOut_BandingCompare(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.c_uint(0),
            ctypes.c_uint(0),
            ctypes.c_uint(polarity),
        )
        self._check_result("Mb_E4O4TrigOut_BandingCompare", result)
        logger.info(
            "E4O4线性比较器已关闭并解绑: slave=%d, comparator=%d",
            slave_no,
            line_compare_no,
        )

    def configure_pre_compare(
        self,
        slave_no: int,
        encoder_no: int,
        precompare_no: int,
        trigger_no: int,
        positions: list[int] | tuple[int, ...],
        direction: int,
        polarity: int = 0,
    ) -> E4O4PreCompareConfig:
        """配置并回读预设定比较器，但保持比较器关闭。"""

        self._require_channel(slave_no, encoder_no, "编码器")
        self._require_channel(slave_no, precompare_no, "预设定比较器")
        self._require_channel(slave_no, trigger_no, "触发输出")
        if not positions:
            raise ValueError("预设定比较器至少需要一个触发位置")
        if direction not in (0, 1, 2):
            raise ValueError(
                f"预设定比较方向只能是0、1或2: {direction}"
            )
        if polarity not in (0, 1):
            raise ValueError(f"E4O4触发极性只能是0或1: {polarity}")

        normalized_positions = tuple(int(value) for value in positions)
        self.disarm_pre_compare(
            slave_no=slave_no,
            precompare_no=precompare_no,
            trigger_no=trigger_no,
            polarity=polarity,
        )

        result = self._dll.Mb_E4O4PreCmp_ResetTrigData(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
        )
        self._check_result("Mb_E4O4PreCmp_ResetTrigData", result)

        result = self._dll.Mb_E4O4PreCmp_BindingEncoder(
            ctypes.c_int(slave_no),
            ctypes.c_int(encoder_no),
            ctypes.c_int(precompare_no),
        )
        self._check_result("Mb_E4O4PreCmp_BindingEncoder", result)

        result = self._dll.Mb_E4O4PreCmp_SetTrigDir(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
            ctypes.c_int(direction),
        )
        self._check_result("Mb_E4O4PreCmp_SetTrigDir", result)

        position_array = (ctypes.c_int * len(normalized_positions))(
            *normalized_positions
        )
        result = self._dll.Mb_E4O4PreCmp_SetTrigData(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
            position_array,
            ctypes.c_int(len(normalized_positions)),
        )
        self._check_result("Mb_E4O4PreCmp_SetTrigData", result)

        pre_mask = 1 << precompare_no
        result = self._dll.Mb_E4O4TrigOut_BandingCompare(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.c_uint(0),
            ctypes.c_uint(pre_mask),
            ctypes.c_uint(polarity),
        )
        self._check_result("Mb_E4O4TrigOut_BandingCompare", result)
        self.reset_trigger_count(slave_no, trigger_no)

        actual_encoder = ctypes.c_int()
        result = self._dll.Mb_E4O4PreCmp_GetBindingEncoder(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
            ctypes.byref(actual_encoder),
        )
        self._check_result("Mb_E4O4PreCmp_GetBindingEncoder", result)

        actual_count = ctypes.c_int()
        result = self._dll.Mb_E4O4PreCmp_GetTrigDataCnt(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
            ctypes.byref(actual_count),
        )
        self._check_result("Mb_E4O4PreCmp_GetTrigDataCnt", result)
        if actual_count.value != len(normalized_positions):
            raise LctStateError(
                "E4O4预设定比较点数量回读不一致: "
                f"expected={len(normalized_positions)}, "
                f"actual={actual_count.value}"
            )

        actual_array = (ctypes.c_int * actual_count.value)()
        result = self._dll.Mb_E4O4PreCmp_GetTrigData(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
            actual_array,
        )
        self._check_result("Mb_E4O4PreCmp_GetTrigData", result)
        actual_positions = tuple(int(value) for value in actual_array)
        trigger_config = self.get_trigger_config(slave_no, trigger_no)
        if (
            actual_encoder.value != encoder_no
            or actual_positions != normalized_positions
            or trigger_config.line_compare_mask != 0
            or trigger_config.precompare_mask != pre_mask
            or trigger_config.polarity != polarity
        ):
            raise LctStateError(
                "E4O4预设定比较器配置回读不一致: "
                f"encoder={actual_encoder.value}, "
                f"positions={actual_positions}, "
                f"trigger={trigger_config}"
            )

        return E4O4PreCompareConfig(
            encoder_no=encoder_no,
            precompare_no=precompare_no,
            trigger_no=trigger_no,
            direction=direction,
            positions=normalized_positions,
        )

    def arm_pre_compare(self, slave_no: int, precompare_no: int) -> None:
        """使能指定预设定比较器。"""

        self._require_channel(slave_no, precompare_no, "预设定比较器")
        result = self._dll.Mb_E4O4PreCmp_SetEnable(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
            ctypes.c_int(1),
        )
        self._check_result("Mb_E4O4PreCmp_SetEnable", result)
        logger.info(
            "E4O4预设定比较器已使能: slave=%d, comparator=%d",
            slave_no,
            precompare_no,
        )

    def disarm_pre_compare(
        self,
        slave_no: int,
        precompare_no: int,
        trigger_no: int,
        polarity: int = 0,
    ) -> None:
        """关闭预设定比较器，并解除触发输出绑定。"""

        self._require_channel(slave_no, precompare_no, "预设定比较器")
        self._require_channel(slave_no, trigger_no, "触发输出")
        result = self._dll.Mb_E4O4PreCmp_SetEnable(
            ctypes.c_int(slave_no),
            ctypes.c_int(precompare_no),
            ctypes.c_int(0),
        )
        self._check_result("Mb_E4O4PreCmp_SetEnable", result)
        result = self._dll.Mb_E4O4TrigOut_BandingCompare(
            ctypes.c_int(slave_no),
            ctypes.c_int(trigger_no),
            ctypes.c_uint(0),
            ctypes.c_uint(0),
            ctypes.c_uint(polarity),
        )
        self._check_result("Mb_E4O4TrigOut_BandingCompare", result)
        logger.info(
            "E4O4预设定比较器已关闭并解绑: slave=%d, comparator=%d",
            slave_no,
            precompare_no,
        )

    def _bind_functions(self) -> None:
        if self._dll is None:
            raise LctStateError("E4O4 DLL尚未加载")

        int_pointer = ctypes.POINTER(ctypes.c_int)
        uint_pointer = ctypes.POINTER(ctypes.c_uint)
        byte_pointer = ctypes.POINTER(ctypes.c_ubyte)

        self._dll.Mb_InitEcat.argtypes = [int_pointer, ctypes.c_int]
        self._dll.Mb_InitEcat.restype = ctypes.c_int
        self._dll.Mb_SelectNetCard.argtypes = [ctypes.c_char_p]
        self._dll.Mb_SelectNetCard.restype = None
        self._dll.Mb_CloseEcat.argtypes = []
        self._dll.Mb_CloseEcat.restype = None
        self._dll.Mb_GetConnectStatus.argtypes = [ctypes.c_int, int_pointer]
        self._dll.Mb_GetConnectStatus.restype = ctypes.c_int
        self._dll.Mb_GetSlaveName.argtypes = [ctypes.c_int]
        self._dll.Mb_GetSlaveName.restype = ctypes.c_void_p
        self._dll.Mb_GetVersion.argtypes = [
            ctypes.c_int,
            byte_pointer,
            byte_pointer,
        ]
        self._dll.Mb_GetVersion.restype = ctypes.c_int
        self._dll.Mb_GetSlaveResource.argtypes = [
            ctypes.c_int,
            int_pointer,
            int_pointer,
            int_pointer,
            int_pointer,
            int_pointer,
            int_pointer,
            int_pointer,
        ]
        self._dll.Mb_GetSlaveResource.restype = ctypes.c_int
        self._dll.Mb_E4O4Encoder_GetEncoderData.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4Encoder_GetEncoderData.restype = ctypes.c_int
        self._dll.Mb_E4O4Encoder_GetParam.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
            int_pointer,
            int_pointer,
        ]
        self._dll.Mb_E4O4Encoder_GetParam.restype = ctypes.c_int
        self._dll.Mb_E4O4Encoder_Initial.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4Encoder_Initial.restype = ctypes.c_int
        self._dll.Mb_E4O4Encoder_GetFilterCount.argtypes = [
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4Encoder_GetFilterCount.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_GetOutMode.argtypes = [
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4TrigOut_GetOutMode.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_GetTrigMode.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4TrigOut_GetTrigMode.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_GetPulseWidth.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4TrigOut_GetPulseWidth.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_GetBanding.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            uint_pointer,
            uint_pointer,
            int_pointer,
        ]
        self._dll.Mb_E4O4TrigOut_GetBanding.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_GetCounter.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4TrigOut_GetCounter.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_ResetCounter.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4TrigOut_ResetCounter.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_BandingCompare.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self._dll.Mb_E4O4TrigOut_BandingCompare.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_SetOutMode.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4TrigOut_SetOutMode.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_SetTrigMode.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4TrigOut_SetTrigMode.restype = ctypes.c_int
        self._dll.Mb_E4O4TrigOut_SetPulseWidth.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4TrigOut_SetPulseWidth.restype = ctypes.c_int
        self._dll.Mb_E4O4LineCmp_BingdingEncoder.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4LineCmp_BingdingEncoder.restype = ctypes.c_int
        self._dll.Mb_E4O4LineCmp_GetBingdingEncoder.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4LineCmp_GetBingdingEncoder.restype = ctypes.c_int
        self._dll.Mb_E4O4LineCmp_SetTriggerData.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4LineCmp_SetTriggerData.restype = ctypes.c_int
        self._dll.Mb_E4O4LineCmp_GetTriggerData.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
            int_pointer,
            int_pointer,
        ]
        self._dll.Mb_E4O4LineCmp_GetTriggerData.restype = ctypes.c_int
        self._dll.Mb_E4O4LineCmp_SetEnable.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4LineCmp_SetEnable.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_ResetTrigData.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4PreCmp_ResetTrigData.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_BindingEncoder.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4PreCmp_BindingEncoder.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_GetBindingEncoder.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4PreCmp_GetBindingEncoder.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_SetTrigDir.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4PreCmp_SetTrigDir.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_SetTrigData.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4PreCmp_SetTrigData.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_GetTrigDataCnt.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4PreCmp_GetTrigDataCnt.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_GetTrigData.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            int_pointer,
        ]
        self._dll.Mb_E4O4PreCmp_GetTrigData.restype = ctypes.c_int
        self._dll.Mb_E4O4PreCmp_SetEnable.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._dll.Mb_E4O4PreCmp_SetEnable.restype = ctypes.c_int

    def _require_loaded(self) -> None:
        if self._dll is None:
            raise LctStateError("E4O4 DLL尚未加载，请先调用load()")

    def _require_slave(self, slave_no: int) -> None:
        self._require_loaded()
        if not self._connected:
            raise LctStateError("E4O4总线尚未连接，请先调用connect()")
        if slave_no <= 0 or slave_no > self._slave_count:
            raise ValueError(
                f"E4O4从站号超出范围: slave={slave_no}, "
                f"slave_count={self._slave_count}"
            )

    def _require_channel(
        self,
        slave_no: int,
        channel_no: int,
        description: str,
    ) -> None:
        self._require_slave(slave_no)
        if channel_no < 0:
            raise ValueError(f"E4O4{description}通道不能小于0: {channel_no}")

    @staticmethod
    def _encode_sdk_text(value: str) -> bytes:
        try:
            return value.encode("mbcs")
        except LookupError:
            return value.encode("gbk")

    @staticmethod
    def _decode_sdk_text(value: bytes) -> str:
        for encoding in ("utf-8", "gbk"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("latin-1", errors="replace")

    @staticmethod
    def _check_result(operation: str, result: int) -> None:
        """MiniEcatLib约定负数为错误，0及正数为非错误返回。"""

        code = int(result)
        if code < 0:
            raise LctSdkCallError(
                device="E4O4",
                operation=operation,
                error_code=code,
            )
