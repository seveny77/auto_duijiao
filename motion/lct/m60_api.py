# -*- coding: utf-8 -*-
"""凌臣M60运动控制SDK的底层Python封装。"""

import ctypes
import logging
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
import time

from motion.lct.errors import (
    LctLibraryLoadError,
    LctSdkCallError,
    LctStateError,
    LctSafetyError,
)


logger = logging.getLogger(__name__)


M60_SUCCESS = 0

M60_ERROR_MESSAGES = {
    0: "执行成功",
    1: "执行错误",
    2: "固件未授权",
    3: "接口参数错误",
    4: "设备未打开",
    5: "EtherCAT从站未连接",
    6: "设备掉线",
    7: "FPGA指令执行超时",
    8: "SDO操作返回超时",
    9: "设备驱动故障",
    10: "文件打开失败",
    11: "文件操作失败",
    12: "系统资源不足",
    13: "尚未加载ENI文件",
    14: "指令未定义",
    15: "数据校验错误",
    16: "指令数据写入超时",
    17: "指令数据读取超时",
    19: "伺服未使能",
    20: "从站别名冲突",
    21: "ENI文件中找不到对应从站",
    22: "看门狗超时",
    23: "急停信号已触发",
    30: "EtherCAT网络拓扑发生变化",
}

class _M60SlaveResource(ctypes.Structure):
    """与厂家SL_RES结构体保持相同的内存布局。"""

    _fields_ = [
        ("slave_num", ctypes.c_int),
        ("axis_num", ctypes.c_int),
        ("io_slave_num", ctypes.c_int),
        ("di_num", ctypes.c_int),
        ("do_num", ctypes.c_int),
        ("ai_num", ctypes.c_int),
        ("ao_num", ctypes.c_int),
        ("input_variable_num", ctypes.c_int),
        ("output_variable_num", ctypes.c_int),
    ]


@dataclass(frozen=True)
class M60SlaveResource:
    """供上层Python代码使用的EtherCAT从站资源信息。"""

    slave_num: int
    axis_num: int
    io_slave_num: int
    di_num: int
    do_num: int
    ai_num: int
    ao_num: int
    input_variable_num: int
    output_variable_num: int


@dataclass(frozen=True)
class M60AxisStatus:
    """由M_GetSts返回的Axis状态位。"""

    raw: int
    alarm: bool
    servo_enabled: bool
    positive_limit: bool
    origin: bool
    negative_limit: bool
    moving: bool
    in_position: bool
    offline: bool
    smooth_stop: bool
    homing_error: bool
    homing_completed: bool
    target_reached: bool

    @classmethod
    def from_raw(cls, raw: int) -> "M60AxisStatus":
        raw = int(raw)
        return cls(
            raw=raw,
            alarm=bool(raw & 0x02),
            servo_enabled=bool(raw & 0x200),
            positive_limit=bool(raw & 0x20),
            origin=bool(raw & 0x100000),
            negative_limit=bool(raw & 0x40),
            moving=bool(raw & 0x400),
            in_position=bool(raw & 0x800),
            offline=bool(raw & 0x1000000),
            smooth_stop=bool(raw & 0x80),
            homing_error=bool(raw & 0x10000),
            homing_completed=bool(raw & 0x20000),
            target_reached=bool(raw & 0x40000),
        )


@dataclass(frozen=True)
class M60HomingParameters:
    """驱动器CiA 402回零参数。"""

    method: int
    offset: int
    speed1: int
    speed2: int
    acceleration: int
    probe_function: int

class M60Api:
    """ecat_motion.dll的底层调用封装。

    创建对象时只保存DLL路径，不会自动加载DLL，也不会打开板卡。
    调用顺序为：

        api = M60Api(dll_path)
        api.load()
        api.open(card_no=0)
        ...
        api.close()
    """

    def __init__(self, dll_path: str):
        self._dll_path = Path(dll_path)

        self._dll = None
        self._dll_directory_handle = None

        self._opened_card: Optional[int] = None
        self._eni_loaded = False
        self._ecat_connected = False
        self._axis_params_loaded = False

    @property
    def dll_path(self) -> Path:
        """返回当前配置的M60 DLL路径。"""

        return self._dll_path

    @property
    def is_loaded(self) -> bool:
        """返回M60 DLL是否已经加载。"""

        return self._dll is not None

    @property
    def is_open(self) -> bool:
        """返回M60板卡是否已经打开。"""

        return self._opened_card is not None

    @property
    def opened_card(self) -> Optional[int]:
        """返回当前已经打开的卡号。"""

        return self._opened_card

    @property
    def eni_loaded(self) -> bool:
        """返回M60是否已经成功加载ENI配置。"""

        return self._eni_loaded

    @property
    def ecat_connected(self) -> bool:
        """返回M60 EtherCAT总线是否已经连接。"""

        return self._ecat_connected

    @property
    def axis_params_loaded(self) -> bool:
        """返回M60轴参数文件是否已经加载。"""

        return self._axis_params_loaded

    def load(self) -> None:
        """加载ecat_motion.dll并绑定基础函数签名。"""

        if self._dll is not None:
            return

        if not self._dll_path.is_file():
            raise LctLibraryLoadError(
                f"M60 DLL不存在: {self._dll_path}"
            )

        try:
            # Python 3.8以后，Windows不会再默认搜索DLL所在目录中的
            # 其他依赖库。因此需要把厂家DLL目录加入当前进程的
            # DLL搜索路径，并保存返回的句柄。
            if (
                os.name == "nt"
                and hasattr(os, "add_dll_directory")
            ):
                self._dll_directory_handle = (
                    os.add_dll_directory(
                        str(self._dll_path.parent)
                    )
                )

            # C# Demo中M60函数使用CallingConvention.Cdecl，
            # 因此Python中使用ctypes.CDLL。
            self._dll = ctypes.CDLL(
                str(self._dll_path)
            )

            self._bind_functions()

        except (OSError, AttributeError) as error:
            self._dll = None

            python_bits = (
                ctypes.sizeof(ctypes.c_void_p) * 8
            )

            raise LctLibraryLoadError(
                "加载M60 DLL失败: "
                f"path={self._dll_path}, "
                f"Python={python_bits}位, "
                f"原因={error}"
            ) from error

        logger.info(
            "M60 DLL加载成功: %s",
            self._dll_path,
        )

    def load_eni(
            self,
            eni_path: str,
    ) -> None:
        """加载M60的EtherCAT ENI配置文件。

        本方法只加载网络配置，不连接EtherCAT，也不会使轴运动。
        """

        self._require_open()

        if self._ecat_connected:
            raise LctStateError(
                "EtherCAT已经连接，不能重新加载ENI，"
                "请先断开EtherCAT"
            )

        path = Path(eni_path)

        if not path.is_file():
            raise FileNotFoundError(
                f"M60 ENI文件不存在: {path}"
            )

        encoded_path = self._encode_sdk_path(
            path
        )

        result = self._dll.M_LoadEni(
            encoded_path,
            ctypes.c_short(self._opened_card),
        )

        self._check_result(
            operation="M_LoadEni",
            result=result,
        )

        self._eni_loaded = True

        self._axis_params_loaded = False

        logger.info(
            "M60 ENI加载成功: card=%d, path=%s",
            self._opened_card,
            path,
        )

    def reset_fpga(self) -> None:
        """复位M60板卡FPGA。

        厂家Demo要求在常规连接EtherCAT之前执行本操作，并在成功后
        等待至少500毫秒。等待动作由后续上层初始化流程负责。
        """

        self._require_open()
        if self._ecat_connected:
            raise LctStateError(
                "EtherCAT已经连接，不能直接复位FPGA，"
                "请先断开EtherCAT"
            )

        result = self._dll.M_ResetFpga(
            ctypes.c_short(self._opened_card)
        )

        self._check_result(
            operation="M_ResetFpga",
            result=result,
        )

        logger.info(
            "M60 FPGA复位完成: card=%d",
            self._opened_card,
        )

    def connect_ecat(
            self,
            option: int = 0,
    ) -> None:
        """连接M60管理的EtherCAT总线。

        Args:
            option:
                连接选项。当前正式流程固定使用0，与现场验证Demo一致。
        """

        self._require_open()

        if not self._eni_loaded:
            raise LctStateError(
                "尚未加载ENI，不能连接EtherCAT"
            )

        if self._ecat_connected:
            return

        if option not in (0, 1):
            raise ValueError(
                f"EtherCAT连接选项只能是0或1: {option}"
            )

        result = self._dll.M_ConnectECAT(
            ctypes.c_short(option),
            ctypes.c_short(self._opened_card),
        )

        self._check_result(
            operation="M_ConnectECAT",
            result=result,
        )

        self._ecat_connected = True
        self._axis_params_loaded = False

        logger.info(
            "M60 EtherCAT连接成功: card=%d, option=%d",
            self._opened_card,
            option,
        )

    def disconnect_ecat(self) -> None:
        """断开M60管理的EtherCAT总线。"""

        if not self._ecat_connected:
            return

        self._require_open()

        result = self._dll.M_DisconnectECAT(
            ctypes.c_short(self._opened_card)
        )

        self._check_result(
            operation="M_DisconnectECAT",
            result=result,
        )

        self._ecat_connected = False
        self._axis_params_loaded = False

        logger.info(
            "M60 EtherCAT已断开: card=%d",
            self._opened_card,
        )

    def load_axis_params(
            self,
            param_path: str,
    ) -> None:
        """从ParamCard0.ini加载M60轴参数。"""

        self._require_ecat_connected()

        path = Path(param_path)

        if not path.is_file():
            raise FileNotFoundError(
                f"M60轴参数文件不存在: {path}"
            )

        encoded_path = self._encode_sdk_path(
            path
        )

        result = self._dll.M_LoadParamFromFile(
            encoded_path,
            ctypes.c_short(self._opened_card),
        )

        self._check_result(
            operation="M_LoadParamFromFile",
            result=result,
        )

        self._axis_params_loaded = True

        logger.info(
            "M60轴参数加载成功: card=%d, path=%s",
            self._opened_card,
            path,
        )

    def get_slave_resource(
            self,
    ) -> M60SlaveResource:
        """读取当前EtherCAT网络中的从站和轴资源。"""

        self._require_ecat_connected()

        raw_resource = _M60SlaveResource()

        result = self._dll.M_GetSlaveResource(
            ctypes.byref(raw_resource),
            ctypes.c_short(self._opened_card),
        )

        self._check_result(
            operation="M_GetSlaveResource",
            result=result,
        )

        resource = M60SlaveResource(
            slave_num=raw_resource.slave_num,
            axis_num=raw_resource.axis_num,
            io_slave_num=raw_resource.io_slave_num,
            di_num=raw_resource.di_num,
            do_num=raw_resource.do_num,
            ai_num=raw_resource.ai_num,
            ao_num=raw_resource.ao_num,
            input_variable_num=(
                raw_resource.input_variable_num
            ),
            output_variable_num=(
                raw_resource.output_variable_num
            ),
        )

        logger.info(
            "M60 EtherCAT资源: "
            "slave=%d, axis=%d, io_slave=%d, "
            "DI=%d, DO=%d, AI=%d, AO=%d",
            resource.slave_num,
            resource.axis_num,
            resource.io_slave_num,
            resource.di_num,
            resource.do_num,
            resource.ai_num,
            resource.ao_num,
        )

        return resource

    def _require_ecat_connected(self) -> None:
        """确认M60 EtherCAT总线已经连接。"""

        self._require_open()

        if not self._ecat_connected:
            raise LctStateError(
                "M60 EtherCAT尚未连接，"
                "请先调用connect_ecat()"
            )

    def get_encoder_position(
            self,
            axis_no: int,
    ) -> float:
        """读取指定轴的M60编码器位置，返回厂家原始计数。"""

        self._require_ecat_connected()

        if axis_no <= 0:
            raise ValueError(
                f"M60轴号必须大于0: {axis_no}"
            )

        position = ctypes.c_double()

        result = self._dll.M_GetEncPos(
            ctypes.c_short(axis_no),
            ctypes.byref(position),
            ctypes.c_short(1),
            ctypes.c_short(self._opened_card),
        )

        self._check_result(
            operation="M_GetEncPos",
            result=result,
        )

        return float(position.value)

    def get_emergency_stop(self) -> bool:
        """读取M60板卡急停状态；True表示急停已触发。"""

        self._require_open()
        emergency = ctypes.c_short()
        result = self._dll.M_GetEmg(
            ctypes.byref(emergency),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_GetEmg", result)
        return bool(emergency.value)

    def get_axis_status(self, axis_no: int) -> M60AxisStatus:
        """读取并解码M60轴状态。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        raw_status = ctypes.c_int()
        result = self._dll.M_GetSts(
            ctypes.c_short(axis_no),
            ctypes.byref(raw_status),
            ctypes.c_short(1),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_GetSts", result)
        return M60AxisStatus.from_raw(raw_status.value)

    def get_drive_status_word(self, axis_no: int) -> int:
        """读取EtherCAT驱动器CiA 402状态字。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        status_word = ctypes.c_ushort()
        result = self._dll.M_EcatStatusWord(
            ctypes.c_short(axis_no),
            ctypes.byref(status_word),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_EcatStatusWord", result)
        return int(status_word.value)

    def get_actual_position(self, axis_no: int) -> int:
        """读取驱动器反馈的实际位置原始计数。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        position = ctypes.c_int()
        result = self._dll.M_ReadActualPosition(
            ctypes.c_short(axis_no),
            ctypes.byref(position),
            ctypes.c_short(1),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_ReadActualPosition", result)
        return int(position.value)

    def get_soft_limits(self, axis_no: int) -> tuple[int, int]:
        """读取软件限位，返回(负向限位, 正向限位)原始计数。"""

        self._require_ecat_connected()
        if not self._axis_params_loaded:
            raise LctStateError(
                "M60轴参数尚未加载，不能读取有效软件限位"
            )
        self._validate_axis_no(axis_no)
        positive = ctypes.c_int()
        negative = ctypes.c_int()
        result = self._dll.M_GetSoftLimit(
            ctypes.c_short(axis_no),
            ctypes.byref(positive),
            ctypes.byref(negative),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_GetSoftLimit", result)
        return int(negative.value), int(positive.value)

    def clear_axis_status(self, axis_no: int) -> None:
        """清除指定轴的报警和可清除状态位。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        result = self._dll.M_ClrSts(
            ctypes.c_short(axis_no),
            ctypes.c_short(1),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_ClrSts", result)
        logger.info("M60轴状态复位完成: axis=%d", axis_no)

    def get_homing_parameters(
        self,
        axis_no: int,
    ) -> M60HomingParameters:
        """读取驱动器当前回零参数，不修改驱动器。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        method = ctypes.c_short()
        offset = ctypes.c_int()
        speed1 = ctypes.c_uint()
        speed2 = ctypes.c_uint()
        acceleration = ctypes.c_uint()
        probe_function = ctypes.c_ushort()
        result = self._dll.M_GetHomingPrm(
            ctypes.c_short(axis_no),
            ctypes.byref(method),
            ctypes.byref(offset),
            ctypes.byref(speed1),
            ctypes.byref(speed2),
            ctypes.byref(acceleration),
            ctypes.byref(probe_function),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_GetHomingPrm", result)
        return M60HomingParameters(
            method=int(method.value),
            offset=int(offset.value),
            speed1=int(speed1.value),
            speed2=int(speed2.value),
            acceleration=int(acceleration.value),
            probe_function=int(probe_function.value),
        )

    def set_homing_mode(self, axis_no: int, mode: int) -> None:
        """切换驱动器回零/位置工作模式。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        result = self._dll.M_SetHomingMode(
            ctypes.c_short(axis_no),
            ctypes.c_short(mode),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_SetHomingMode", result)
        logger.info("M60工作模式已切换: axis=%d, mode=%d", axis_no, mode)

    def start_homing(self, axis_no: int) -> None:
        """启动指定轴的驱动器回零动作。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        result = self._dll.M_HomingStart(
            ctypes.c_short(axis_no),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_HomingStart", result)
        logger.info("M60回零已启动: axis=%d", axis_no)

    def cancel_homing(self, axis_no: int) -> None:
        """取消指定轴的回零动作。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        result = self._dll.M_HomeCancelSingleAxis(
            ctypes.c_short(axis_no),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_HomeCancelSingleAxis", result)
        logger.warning("M60回零取消命令已发出: axis=%d", axis_no)

    def get_command_position(self, axis_no: int) -> float:
        """读取板卡当前规划位置。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        position = ctypes.c_double()
        result = self._dll.M_GetCmd(
            ctypes.c_short(axis_no),
            ctypes.byref(position),
            ctypes.c_short(1),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_GetCmd", result)
        return float(position.value)

    def servo_on(
        self,
        axis_no: int,
        timeout_s: float = 2.0,
    ) -> None:
        """使能指定轴，并等待状态位确认。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        if timeout_s <= 0:
            raise ValueError(f"使能等待时间必须大于0: {timeout_s}")

        status = self.get_axis_status(axis_no)
        self._raise_if_unsafe_for_motion(axis_no, status)
        if status.servo_enabled:
            return

        result = self._dll.M_Servo_On(
            ctypes.c_short(axis_no),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_Servo_On", result)

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = self.get_axis_status(axis_no)
            self._raise_if_unsafe_for_motion(axis_no, status)
            if status.servo_enabled:
                logger.info("M60轴已使能: axis=%d", axis_no)
                return
            time.sleep(0.02)

        raise LctStateError(
            f"M60轴使能状态确认超时: axis={axis_no}"
        )

    def servo_off(self, axis_no: int) -> None:
        """关闭指定轴伺服使能。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        result = self._dll.M_Servo_Off(
            ctypes.c_short(axis_no),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_Servo_Off", result)
        logger.info("M60轴已去使能: axis=%d", axis_no)

    def absolute_move(
        self,
        axis_no: int,
        target_counts: int,
        velocity_counts_s: float,
    ) -> None:
        """发出单轴绝对运动命令，不在本方法内等待完成。"""

        self._require_ecat_connected()
        if not self._axis_params_loaded:
            raise LctStateError(
                "M60轴参数尚未加载，不能执行绝对运动"
            )
        self._validate_axis_no(axis_no)
        if velocity_counts_s <= 0:
            raise ValueError(
                f"绝对运动速度必须大于0: {velocity_counts_s}"
            )

        status = self.get_axis_status(axis_no)
        self._raise_if_unsafe_for_motion(axis_no, status)
        if not status.servo_enabled:
            raise LctStateError(
                f"M60轴尚未使能，不能运动: axis={axis_no}"
            )

        negative_limit, positive_limit = self.get_soft_limits(axis_no)
        if not negative_limit <= target_counts <= positive_limit:
            raise LctSafetyError(
                "M60目标位置超出软件限位: "
                f"axis={axis_no}, target={target_counts}, "
                f"range=[{negative_limit}, {positive_limit}]"
            )

        result = self._dll.M_AbsMove(
            ctypes.c_short(axis_no),
            ctypes.c_int(target_counts),
            ctypes.c_double(velocity_counts_s),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_AbsMove", result)
        logger.info(
            "M60绝对运动已启动: axis=%d, target=%d, velocity=%.3f",
            axis_no,
            target_counts,
            velocity_counts_s,
        )

    def wait_motion_complete(
        self,
        axis_no: int,
        target_counts: int,
        timeout_s: float,
        tolerance_counts: int = 100,
        poll_interval_s: float = 0.02,
        cancel_event=None,
    ) -> int:
        """监控轴直到停止并到达目标，返回最终实际位置。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        if timeout_s <= 0:
            raise ValueError(f"运动超时时间必须大于0: {timeout_s}")
        if tolerance_counts < 0:
            raise ValueError(
                f"到位容差不能小于0: {tolerance_counts}"
            )
        if poll_interval_s <= 0:
            raise ValueError(
                f"轮询周期必须大于0: {poll_interval_s}"
            )

        deadline = time.monotonic() + timeout_s
        saw_motion = False
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("用户取消")

            if self.get_emergency_stop():
                raise LctSafetyError(
                    f"M60运动期间检测到急停: axis={axis_no}"
                )

            status = self.get_axis_status(axis_no)
            self._raise_if_unsafe_for_motion(axis_no, status)
            actual = self.get_actual_position(axis_no)
            saw_motion = saw_motion or status.moving

            if (
                not status.moving
                and abs(actual - target_counts) <= tolerance_counts
            ):
                logger.info(
                    "M60运动完成: axis=%d, target=%d, actual=%d, "
                    "saw_motion=%s",
                    axis_no,
                    target_counts,
                    actual,
                    saw_motion,
                )
                return actual

            time.sleep(poll_interval_s)

        raise TimeoutError(
            "M60运动完成等待超时: "
            f"axis={axis_no}, target={target_counts}, "
            f"actual={self.get_actual_position(axis_no)}"
        )

    def stop(self, axis_no: int, emergency: bool = False) -> None:
        """停止指定轴；默认平滑停止，emergency=True使用急停减速度。"""

        self._require_ecat_connected()
        self._validate_axis_no(axis_no)
        result = self._dll.M_StopSingleAxis(
            ctypes.c_short(axis_no),
            ctypes.c_int(1 if emergency else 0),
            ctypes.c_short(self._opened_card),
        )
        self._check_result("M_StopSingleAxis", result)
        logger.warning(
            "M60轴停止命令已发出: axis=%d, emergency=%s",
            axis_no,
            emergency,
        )

    @staticmethod
    def _raise_if_unsafe_for_motion(
        axis_no: int,
        status: M60AxisStatus,
    ) -> None:
        problems = []
        if status.alarm:
            problems.append("驱动器报警")
        if status.positive_limit:
            problems.append("正极限")
        if status.negative_limit:
            problems.append("负极限")
        if status.offline:
            problems.append("轴掉线")
        if problems:
            raise LctSafetyError(
                f"M60轴状态不允许运动: axis={axis_no}, "
                f"problems={','.join(problems)}, "
                f"raw=0x{status.raw:08X}"
            )

    @staticmethod
    def _validate_axis_no(axis_no: int) -> None:
        if axis_no <= 0:
            raise ValueError(
                f"M60轴号必须大于0: {axis_no}"
            )

    def open(self, card_no: int = 0) -> None:
        """打开指定M60板卡。

        M_Open只负责打开板卡，不加载ENI、不连接EtherCAT、
        不使能伺服，也不会发出运动指令。
        """

        self._require_loaded()

        if card_no < 0:
            raise ValueError(
                f"M60卡号不能小于0: {card_no}"
            )

        if self._opened_card is not None:
            if self._opened_card == card_no:
                return

            raise LctStateError(
                "M60已有其他板卡处于打开状态: "
                f"opened={self._opened_card}, "
                f"requested={card_no}"
            )

        result = self._dll.M_Open(
            ctypes.c_short(card_no),
            ctypes.c_short(0),
        )

        self._check_result(
            operation="M_Open",
            result=result,
        )

        self._opened_card = card_no

        logger.info(
            "M60板卡已打开: card=%d",
            card_no,
        )

    def close(self) -> None:
        """关闭当前已经打开的M60板卡。"""

        if self._opened_card is None:
            return
        if self._ecat_connected:
            self.disconnect_ecat()

        self._require_loaded()

        card_no = self._opened_card

        result = self._dll.M_Close(
            ctypes.c_short(card_no)
        )

        self._check_result(
            operation="M_Close",
            result=result,
        )

        self._opened_card = None
        self._eni_loaded = False
        self._ecat_connected = False
        self._axis_params_loaded = False

        logger.info(
            "M60板卡已关闭: card=%d",
            card_no,
        )

    def get_version(
        self,
        buffer_size: int = 256,
    ) -> str:
        """读取M60板卡和SDK版本字符串。

        调用本方法前必须先执行open()。
        """

        self._require_open()

        if buffer_size <= 1:
            raise ValueError(
                "版本字符串缓冲区必须大于1字节"
            )

        version_buffer = ctypes.create_string_buffer(
            buffer_size
        )

        result = self._dll.M_GetVersion(
            version_buffer,
            ctypes.c_int(buffer_size),
            ctypes.c_short(self._opened_card),
        )

        self._check_result(
            operation="M_GetVersion",
            result=result,
        )

        raw_version = version_buffer.value

        try:
            return raw_version.decode("utf-8")
        except UnicodeDecodeError:
            return raw_version.decode(
                "gbk",
                errors="replace",
            )

    def _bind_functions(self) -> None:
        """声明当前使用的厂家函数参数和返回值类型。"""

        if self._dll is None:
            raise LctStateError(
                "M60 DLL尚未加载"
            )

        # short M_Open(short card, short param)
        self._dll.M_Open.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_Open.restype = ctypes.c_short

        # short M_Close(short card)
        self._dll.M_Close.argtypes = [
            ctypes.c_short,
        ]
        self._dll.M_Close.restype = ctypes.c_short

        # short M_GetVersion(
        #     byte* pVersion,
        #     int size,
        #     short card
        # )
        self._dll.M_GetVersion.argtypes = [
            ctypes.POINTER(ctypes.c_char),
            ctypes.c_int,
            ctypes.c_short,
        ]
        self._dll.M_GetVersion.restype = ctypes.c_short

        # short M_LoadEni(
        #     char* eniPath,
        #     short card
        # )
        self._dll.M_LoadEni.argtypes = [
            ctypes.c_char_p,
            ctypes.c_short,
        ]
        self._dll.M_LoadEni.restype = ctypes.c_short

        # short M_ResetFpga(short card)
        self._dll.M_ResetFpga.argtypes = [
            ctypes.c_short,
        ]
        self._dll.M_ResetFpga.restype = ctypes.c_short

        # short M_ConnectECAT(
        #     short option,
        #     short card
        # )
        self._dll.M_ConnectECAT.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_ConnectECAT.restype = ctypes.c_short

        # short M_DisconnectECAT(short card)
        self._dll.M_DisconnectECAT.argtypes = [
            ctypes.c_short,
        ]
        self._dll.M_DisconnectECAT.restype = ctypes.c_short

        # short M_LoadParamFromFile(
        #     char* filename,
        #     short card
        # )
        self._dll.M_LoadParamFromFile.argtypes = [
            ctypes.c_char_p,
            ctypes.c_short,
        ]
        self._dll.M_LoadParamFromFile.restype = (
            ctypes.c_short
        )

        # short M_GetSlaveResource(
        #     SL_RES* resource,
        #     short card
        # )
        self._dll.M_GetSlaveResource.argtypes = [
            ctypes.POINTER(_M60SlaveResource),
            ctypes.c_short,
        ]
        self._dll.M_GetSlaveResource.restype = (
            ctypes.c_short
        )

        # short M_GetEncPos(
        #     short encoder,
        #     double* value,
        #     short count,
        #     short card
        # )
        self._dll.M_GetEncPos.argtypes = [
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_GetEncPos.restype = ctypes.c_short

        # short M_GetEmg(short* emg, short card)
        self._dll.M_GetEmg.argtypes = [
            ctypes.POINTER(ctypes.c_short),
            ctypes.c_short,
        ]
        self._dll.M_GetEmg.restype = ctypes.c_short

        # short M_GetSts(
        #     short axis, int* status,
        #     short count, short card
        # )
        self._dll.M_GetSts.argtypes = [
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_GetSts.restype = ctypes.c_short

        # short M_EcatStatusWord(
        #     short axis, ushort* statusword, short card
        # )
        self._dll.M_EcatStatusWord.argtypes = [
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.c_short,
        ]
        self._dll.M_EcatStatusWord.restype = ctypes.c_short

        # short M_ReadActualPosition(
        #     short axis, int* position,
        #     short count, short card
        # )
        self._dll.M_ReadActualPosition.argtypes = [
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_ReadActualPosition.restype = ctypes.c_short

        # short M_GetSoftLimit(
        #     short axis, int* positive,
        #     int* negative, short card
        # )
        self._dll.M_GetSoftLimit.argtypes = [
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_short,
        ]
        self._dll.M_GetSoftLimit.restype = ctypes.c_short

        self._dll.M_ClrSts.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_ClrSts.restype = ctypes.c_short

        self._dll.M_GetHomingPrm.argtypes = [
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_short),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_ushort),
            ctypes.c_short,
        ]
        self._dll.M_GetHomingPrm.restype = ctypes.c_short

        self._dll.M_SetHomingMode.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_SetHomingMode.restype = ctypes.c_short

        self._dll.M_HomingStart.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_HomingStart.restype = ctypes.c_short

        self._dll.M_HomeCancelSingleAxis.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_HomeCancelSingleAxis.restype = ctypes.c_short

        self._dll.M_GetCmd.argtypes = [
            ctypes.c_short,
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_GetCmd.restype = ctypes.c_short

        self._dll.M_Servo_On.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_Servo_On.restype = ctypes.c_short

        self._dll.M_Servo_Off.argtypes = [
            ctypes.c_short,
            ctypes.c_short,
        ]
        self._dll.M_Servo_Off.restype = ctypes.c_short

        self._dll.M_AbsMove.argtypes = [
            ctypes.c_short,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_short,
        ]
        self._dll.M_AbsMove.restype = ctypes.c_short

        self._dll.M_StopSingleAxis.argtypes = [
            ctypes.c_short,
            ctypes.c_int,
            ctypes.c_short,
        ]
        self._dll.M_StopSingleAxis.restype = ctypes.c_short

    def _require_loaded(self) -> None:
        """确认M60 DLL已经加载。"""

        if self._dll is None:
            raise LctStateError(
                "M60 DLL尚未加载，请先调用load()"
            )

    def _require_open(self) -> None:
        """确认M60板卡已经打开。"""

        self._require_loaded()

        if self._opened_card is None:
            raise LctStateError(
                "M60板卡尚未打开，请先调用open()"
            )


    @staticmethod
    def _encode_sdk_path(path: Path) -> bytes:
        """把Python路径转换成厂家C接口需要的字节字符串。

        厂家C# Demo没有指定Unicode字符集，底层接口按照ANSI字符串
        接收路径。Windows上使用当前系统代码页进行编码。
        """

        absolute_path = path.resolve()

        encoding = (
            "mbcs"
            if os.name == "nt"
            else "utf-8"
        )

        try:
            return str(absolute_path).encode(
                encoding
            )

        except UnicodeEncodeError as error:
            raise ValueError(
                "M60 SDK无法编码文件路径，请把SDK和参数文件放到"
                "不含特殊字符的英文目录中: "
                f"{absolute_path}"
            ) from error

    @staticmethod
    def _check_result(
        operation: str,
        result: int,
    ) -> None:
        """把M60错误码转换成统一的Python异常。"""

        result = int(result)

        if result == M60_SUCCESS:
            return

        detail = M60_ERROR_MESSAGES.get(
            result,
            "未知M60错误",
        )

        raise LctSdkCallError(
            device="M60",
            operation=operation,
            error_code=result,
            detail=detail,
        )


# ── CT 类级插桩 ──
# M60 每个 DLL 往返方法（absolute_move/wait_motion_complete/get_axis_status/
# servo_on 等）计时入 perf 注册表。默认静默；单次 ≥100ms 打 [CT][慢]。
# wait_motion_complete 内部 20ms 轮询，耗时长属正常，仅注册供统计。
import perf as _perf

_perf.instrument_class(M60Api, "hw.m60", slow_ms=100.0)
