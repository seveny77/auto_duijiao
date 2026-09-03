import logging
import os
import sys
import time
import threading
import queue
import numpy as np
import cv2
from ctypes import *

# 优先加载 MVS 运行时 DLL，避免加载到其他旧版 MvCameraControl.dll（如 LBAS 运行时）
_MVS_RUNTIME_DIRS = [
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
    r"C:\Program Files\Common Files\MVS\Runtime\Win64_x64",
]
for _dll_dir in _MVS_RUNTIME_DIRS:
    if os.path.isdir(_dll_dir):
        try:
            os.add_dll_directory(_dll_dir)
        except Exception:
            pass
        os.environ["PATH"] = _dll_dir + os.pathsep + os.environ.get("PATH", "")
        break

from MvImport.MvCameraControl_class import *
from MvImport.CameraParams_header import MVCC_INTVALUE
from camera.frame_converter import _frame_to_numpy


logger = logging.getLogger(__name__)


# 相机 SDK 属于整个 Python 进程，而不是某一个 HikCamera 对象。
_sdk_initialized = False

# 保护 SDK 的 Initialize / Finalize 状态。
#
# 避免两个线程几乎同时打开相机时，
# 重复调用 MV_CC_Initialize()。
_sdk_lifecycle_lock = threading.Lock()

class HikCamera:
    def __init__(self, camera_index=0):
        self._cam = None
        self._index = camera_index
        self._trigger_mode = "unknown"
        self._device_info = None
        self._stream_count = 0
        self._user_callback = None
        self._cb_func_holder = None
        self._is_grabbing = False

    # ========== lifecycle ==========
    def open(self):
        """初始化 SDK、枚举设备、创建句柄并打开相机。

        只有全部步骤成功后，才把相机对象保存到 self._cam。
        任意步骤失败时，都会尽量回收已经创建的 SDK 资源。
        """

        # 不允许同一个 HikCamera 对象重复打开。
        #
        # 如果这里已经有相机对象，说明：
        #   1. 相机可能已经打开；
        #   2. 或者上一次关闭流程没有正确完成。
        #
        # 此时继续覆盖 self._cam，会把旧句柄永久丢失。
        if self._cam is not None:
            raise RuntimeError("相机已经打开，请勿重复调用 open()")
        global _sdk_initialized

        with _sdk_lifecycle_lock:
            if not _sdk_initialized:
                ret = MvCamera.MV_CC_Initialize()

                if ret != 0:
                    raise RuntimeError(
                        f"相机 SDK 初始化失败: 0x{ret:X}"
                    )

                _sdk_initialized = True
                logger.info(
                    "camera SDK initialized"
                )

        # 创建并清空设备列表结构体。
        device_list = MV_CC_DEVICE_INFO_LIST()
        memset(byref(device_list), 0, sizeof(device_list))

        # 当前项目使用的是 USB 相机，因此这里只枚举 USB 设备。
        ret = MvCamera.MV_CC_EnumDevices(
            MV_USB_DEVICE,
            device_list,
        )

        if ret != 0:
            raise RuntimeError(
                f"枚举 USB 相机失败: 0x{ret:X}"
            )

        device_count = int(device_list.nDeviceNum)

        if device_count == 0:
            raise RuntimeError("没有发现 USB 相机")

        # 相机索引必须位于：
        #
        #     0 <= camera_index < 设备数量
        #
        # 例如只找到一台相机时，只允许使用 index=0。
        if self._index < 0 or self._index >= device_count:
            raise RuntimeError(
                f"相机索引越界: "
                f"index={self._index}, "
                f"已发现设备数={device_count}"
            )

        # 从设备列表中取出目标设备的信息。
        #
        # 这里先保存到局部变量，不立即写入 self._device_info，
        # 避免后面的创建或打开失败后，对象留下半初始化状态。
        device_info = cast(
            device_list.pDeviceInfo[self._index],
            POINTER(MV_CC_DEVICE_INFO),
        ).contents

        # 同样先使用局部变量 cam。
        #
        # 只有 CreateHandle 和 OpenDevice 都成功，
        # 才把它交给 self._cam。
        cam = MvCamera()
        handle_created = False

        try:
            # 第一步：根据设备信息创建 SDK 句柄。
            ret = cam.MV_CC_CreateHandle(device_info)

            if ret != 0:
                raise RuntimeError(
                    f"CreateHandle fail: 0x{ret:X}"
                )

            handle_created = True

            # 第二步：通过刚创建的句柄打开设备。
            ret = cam.MV_CC_OpenDevice(
                MV_ACCESS_Exclusive,
                0,
            )

            if ret != 0:
                raise RuntimeError(
                    f"OpenDevice fail: 0x{ret:X}"
                )

        except Exception:
            # 如果句柄已经创建，但后面的 OpenDevice 失败，
            # 必须把创建出来的句柄销毁。
            if handle_created:
                try:
                    destroy_ret = cam.MV_CC_DestroyHandle()

                    if destroy_ret != 0:
                        logger.warning(
                            "打开失败后销毁句柄失败: 0x%X",
                            destroy_ret,
                        )

                except Exception as cleanup_error:
                    logger.warning(
                        "打开失败后的句柄清理异常: %s",
                        cleanup_error,
                    )

            # 保留原始异常，让上层界面能够显示真正的打开失败原因。
            raise

        # 只有创建句柄、打开设备全部成功后，
        # 才正式修改当前 HikCamera 对象的状态。
        self._device_info = device_info
        self._cam = cam
        self._is_grabbing = False
        self._trigger_mode = "unknown"

        logger.info(
            "camera opened: %s",
            self._model_name(),
        )

    @property
    def is_connected(self) -> bool:
        """相机句柄是否处于打开状态。

        只反映 Python 侧的句柄归属，不发起 SDK 调用，
        供 GUI 连接状态判断和任务启动前校验使用。
        """
        return self._cam is not None

    def close(self):
        """关闭相机设备并销毁 SDK 句柄。

        关闭操作允许重复调用。
        即使停止取流失败，也继续尝试关闭和销毁句柄。
        """

        # 已经关闭时直接返回。
        if self._cam is None:
            self._device_info = None
            self._is_grabbing = False
            self._user_callback = None
            self._cb_func_holder = None
            self._trigger_mode = "unknown"
            return

        # 如果仍在取流，优先通过统一的 stop_grabbing() 停止。
        if self._is_grabbing:
            try:
                self.stop_grabbing()
            except Exception as e:
                # close() 属于最终清理路径。
                #
                # 停止取流失败时不能直接放弃后面的关闭和销毁，
                # 否则相机句柄可能永久残留。
                logger.warning(
                    "关闭前停止取流失败: %s",
                    e,
                )

        # 关闭设备连接。
        try:
            ret = self._cam.MV_CC_CloseDevice()

            if ret != 0:
                logger.warning(
                    "CloseDevice fail: 0x%X",
                    ret,
                )

        except Exception as e:
            logger.warning(
                "关闭相机设备异常: %s",
                e,
            )

        # 销毁 SDK 句柄。
        try:
            ret = self._cam.MV_CC_DestroyHandle()

            if ret != 0:
                logger.warning(
                    "DestroyHandle fail: 0x%X",
                    ret,
                )

        except Exception as e:
            logger.warning(
                "销毁相机句柄异常: %s",
                e,
            )

        # 无论 SDK 清理结果如何，都清除 Python 侧引用，
        # 避免后续代码继续使用已经进入关闭流程的旧句柄。
        self._cam = None
        self._device_info = None
        self._is_grabbing = False
        self._user_callback = None
        self._cb_func_holder = None
        self._trigger_mode = "unknown"

        logger.info(
            "camera closed"
        )

    @staticmethod
    def shutdown() -> bool:
        """反初始化整个相机 SDK。

        这个方法只应在所有相机对象已经关闭、
        所有相机工作线程已经退出后调用。

        返回：
            True：SDK 已经处于关闭状态；
            False：SDK 反初始化失败。
        """

        global _sdk_initialized

        # Initialize 和 Finalize 使用同一把锁。
        #
        # 这样不会出现一个线程正在初始化，
        # 另一个线程同时反初始化的情况。
        with _sdk_lifecycle_lock:

            # SDK 本来就没有初始化，目标状态已经满足。
            #
            # 因此允许安全地重复调用 shutdown()。
            if not _sdk_initialized:
                return True

            try:
                ret = MvCamera.MV_CC_Finalize()

            except Exception as e:
                # Finalize 调用本身发生异常时，
                # 保留 _sdk_initialized=True。
                #
                # 因为程序不能假装 SDK 已经成功关闭。
                logger.warning(
                    "相机 SDK 反初始化异常: %s",
                    e,
                )
                return False

            if ret != 0:
                # SDK 返回失败时同样保留 True，
                # 让内部状态反映真实结果。
                logger.warning(
                    "相机 SDK 反初始化失败: 0x%X",
                    ret,
                )
                return False

            # 只有 Finalize 明确成功后，
            # 才把进程级状态改为未初始化。
            _sdk_initialized = False

            logger.info(
                "camera SDK finalized"
            )
            return True

    def _model_name(self):
        raw = bytes(self._device_info.SpecialInfo.stUsb3VInfo.chModelName)
        return raw.decode("gbk").strip("\x00")

    # ========== settings ==========
    def set_exposure(self, value_us) -> bool:
        """设置相机曝光时间，单位为微秒。"""

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                "相机未打开，无法设置曝光时间"
            )

        exposure_us = float(value_us)

        ret = cam.MV_CC_SetFloatValue(
            "ExposureTime",
            exposure_us,
        )

        if ret != 0:
            raise RuntimeError(
                f"设置曝光时间失败: "
                f"value={exposure_us}us, "
                f"error=0x{ret:X}"
            )

        logger.info(
            "exposure: %gus",
            exposure_us,
        )
        return True

    def set_gain(self, value_db) -> bool:
        """设置相机增益，单位为 dB。"""

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                "相机未打开，无法设置增益"
            )

        gain_db = float(value_db)

        ret = cam.MV_CC_SetFloatValue(
            "Gain",
            gain_db,
        )

        if ret != 0:
            raise RuntimeError(
                f"设置增益失败: "
                f"value={gain_db}dB, "
                f"error=0x{ret:X}"
            )

        logger.info(
            "gain: %gdB",
            gain_db,
        )
        return True

    def get_sensor_size(self) -> tuple[int, int]:
        """读取相机当前采样条件下允许的最大宽高。"""

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                "相机未打开，无法读取传感器尺寸"
            )

        width_info = MVCC_INTVALUE()
        height_info = MVCC_INTVALUE()

        width_ret = cam.MV_CC_GetIntValue(
            "WidthMax",
            width_info,
        )

        height_ret = cam.MV_CC_GetIntValue(
            "HeightMax",
            height_info,
        )

        if width_ret == 0 and height_ret == 0:
            width = int(width_info.nCurValue)
            height = int(height_info.nCurValue)
        else:
            # 某些型号可能没有WidthMax/HeightMax节点，
            # 此时回退读取Width/Height节点的nMax。
            width_info = MVCC_INTVALUE()
            height_info = MVCC_INTVALUE()

            width_ret = cam.MV_CC_GetIntValue(
                "Width",
                width_info,
            )

            height_ret = cam.MV_CC_GetIntValue(
                "Height",
                height_info,
            )

            if width_ret != 0 or height_ret != 0:
                raise RuntimeError(
                    "读取相机最大分辨率失败: "
                    f"Width=0x{width_ret:X}, "
                    f"Height=0x{height_ret:X}"
                )

            width = int(width_info.nMax)
            height = int(height_info.nMax)

        if width <= 0 or height <= 0:
            raise RuntimeError(
                f"相机返回了无效尺寸: {width}x{height}"
            )

        logger.info(
            "sensor size: %dx%d",
            width,
            height,
        )

        return width, height

    def set_roi(self, x, y, w, h) -> bool:
        """设置相机 ROI，并读回实际生效值进行校验。"""

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                "相机未打开，无法设置 ROI"
            )

        if self._is_grabbing:
            raise RuntimeError(
                "相机正在取流，必须先停止取流才能修改 ROI"
            )

        # 将可能来自 NumPy、Qt 或配置文件的数值，
        # 统一转换为 Python int。
        roi_x = int(x)
        roi_y = int(y)
        roi_w = int(w)
        roi_h = int(h)

        # 这里只进行与相机型号无关的基础检查。
        #
        # 更具体的最大尺寸、步进和对齐要求，
        # 仍然交给相机 SDK 判断。
        if roi_x < 0 or roi_y < 0:
            raise ValueError(
                f"ROI 偏移不能为负数: "
                f"x={roi_x}, y={roi_y}"
            )

        if roi_w <= 0 or roi_h <= 0:
            raise ValueError(
                f"ROI 宽高必须大于 0: "
                f"w={roi_w}, h={roi_h}"
            )

        # ROI 的设置顺序非常重要：
        #
        #   1. 先把偏移归零；
        #   2. 再设置 Width / Height；
        #   3. 最后移动到目标 OffsetX / OffsetY。
        #
        # 这样既能从小窗口恢复全幅，
        # 也能从全幅缩小并移动到目标位置。
        settings = (
            ("OffsetX", 0),
            ("OffsetY", 0),
            ("Width", roi_w),
            ("Height", roi_h),
            ("OffsetX", roi_x),
            ("OffsetY", roi_y),
        )

        for key, value in settings:
            ret = cam.MV_CC_SetIntValue(
                key,
                value,
            )

            if ret != 0:
                # 任意一个节点设置失败，就立即终止。
                #
                # 不继续打印完整 ROI，
                # 也不让上层 Pipeline 带着半设置状态继续运行。
                raise RuntimeError(
                    f"ROI 设置失败: "
                    f"{key}={value}, "
                    f"目标=({roi_x},{roi_y}) "
                    f"{roi_w}x{roi_h}, "
                    f"error=0x{ret:X}"
                )

        # 设置完成后，再从相机读回四个节点的实际值。
        actual = {}

        for key in (
                "OffsetX",
                "OffsetY",
                "Width",
                "Height",
        ):
            value_info = MVCC_INTVALUE()

            ret = cam.MV_CC_GetIntValue(
                key,
                value_info,
            )

            if ret != 0:
                raise RuntimeError(
                    f"ROI 读回失败: "
                    f"node={key}, "
                    f"error=0x{ret:X}"
                )

            actual[key] = int(value_info.nCurValue)

        actual_roi = (
            actual["OffsetX"],
            actual["OffsetY"],
            actual["Width"],
            actual["Height"],
        )

        expected_roi = (
            roi_x,
            roi_y,
            roi_w,
            roi_h,
        )

        # SDK 返回设置成功后，再确认实际值确实等于请求值。
        if actual_roi != expected_roi:
            raise RuntimeError(
                f"ROI 实际值与请求值不一致: "
                f"请求=({roi_x},{roi_y}) "
                f"{roi_w}x{roi_h}, "
                f"实际=({actual['OffsetX']},"
                f"{actual['OffsetY']}) "
                f"{actual['Width']}x{actual['Height']}"
            )

        logger.info(
            "ROI: (%d,%d) %dx%d",
            actual["OffsetX"],
            actual["OffsetY"],
            actual["Width"],
            actual["Height"],
        )

        return True

    def get_roi(self) -> tuple[int, int, int, int]:
        """读取相机当前实际生效的 ROI。"""

        cam = self._cam
        if cam is None:
            raise RuntimeError("相机未打开，无法读取 ROI")

        values = {}
        for key in ("OffsetX", "OffsetY", "Width", "Height"):
            info = MVCC_INTVALUE()
            ret = cam.MV_CC_GetIntValue(key, info)
            if ret != 0:
                raise RuntimeError(
                    f"ROI 读取失败: node={key}, error=0x{ret:X}"
                )
            values[key] = int(info.nCurValue)

        return (
            values["OffsetX"],
            values["OffsetY"],
            values["Width"],
            values["Height"],
        )

    def _set_sampling_axis(
            self,
            feature_name: str,
            axis: str,
            requested_value: int,
    ):
        """设置 Binning 或 Decimation 的单个方向。

        feature_name:
            "Binning" 或 "Decimation"

        axis:
            "Horizontal" 或 "Vertical"

        返回：
            实际成功使用的节点名、读回的实际值。
        """

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                f"相机未打开，无法设置 "
                f"{feature_name} {axis}"
            )

        # 当前相机优先使用厂商提供的 _Val 节点。
        #
        # 其他型号相机可能只支持标准 GenICam 节点，
        # 因此保留标准节点作为回退。
        candidate_nodes = (
            f"{feature_name}{axis}_Val",
            f"{feature_name}{axis}",
        )

        # 保存每个候选节点的失败状态码。
        #
        # 如果两个节点全部失败，最终错误信息会同时包含
        # 两次尝试的结果，方便判断节点是否存在或是否可写。
        failed_attempts = []

        for node_name in candidate_nodes:
            ret = cam.MV_CC_SetIntValue(
                node_name,
                requested_value,
            )

            if ret != 0:
                failed_attempts.append(
                    f"{node_name}=0x{ret:X}"
                )
                continue

            # 哪个节点设置成功，就从同一个节点读回实际值。
            value_info = MVCC_INTVALUE()

            read_ret = cam.MV_CC_GetIntValue(
                node_name,
                value_info,
            )

            if read_ret != 0:
                raise RuntimeError(
                    f"{feature_name} {axis} 设置成功，"
                    f"但读回失败: "
                    f"node={node_name}, "
                    f"error=0x{read_ret:X}"
                )

            actual_value = int(value_info.nCurValue)

            if actual_value != requested_value:
                raise RuntimeError(
                    f"{feature_name} {axis} 实际值不一致: "
                    f"node={node_name}, "
                    f"请求={requested_value}, "
                    f"实际={actual_value}"
                )

            return node_name, actual_value

        # 两个候选节点都没有成功。
        attempts_text = ", ".join(failed_attempts)

        raise RuntimeError(
            f"{feature_name} {axis} 设置失败: "
            f"请求={requested_value}, "
            f"尝试结果=[{attempts_text}]"
        )

    def _set_sampling_pair(
            self,
            feature_name: str,
            horizontal,
            vertical,
            log_name: str,
    ) -> bool:
        """设置 Binning 或 Decimation 的水平、垂直方向。"""

        if self._cam is None:
            raise RuntimeError(
                f"相机未打开，无法设置 {feature_name}"
            )

        if self._is_grabbing:
            raise RuntimeError(
                f"相机正在取流，必须先停止取流"
                f"才能修改 {feature_name}"
            )

        horizontal_value = int(horizontal)
        vertical_value = int(vertical)

        # Binning 和 Decimation 的倍率至少为 1。
        if horizontal_value <= 0 or vertical_value <= 0:
            raise ValueError(
                f"{feature_name} 倍率必须大于 0: "
                f"horizontal={horizontal_value}, "
                f"vertical={vertical_value}"
            )

        horizontal_node, horizontal_actual = (
            self._set_sampling_axis(
                feature_name,
                "Horizontal",
                horizontal_value,
            )
        )

        vertical_node, vertical_actual = (
            self._set_sampling_axis(
                feature_name,
                "Vertical",
                vertical_value,
            )
        )

        # 只有两个方向都成功设置并读回以后，
        # 才输出最终成功日志。
        logger.info(
            "%s Horizontal 实际: %d "
            "(node=%s)",
            log_name,
            horizontal_actual,
            horizontal_node,
        )

        logger.info(
            "%s Vertical 实际: %d "
            "(node=%s)",
            log_name,
            vertical_actual,
            vertical_node,
        )

        logger.info(
            "%s: %dx%d",
            log_name,
            horizontal_actual,
            vertical_actual,
        )

        return True

    def set_binning(self, h_bin, v_bin) -> bool:
        """设置水平和垂直 Binning。"""

        return self._set_sampling_pair(
            feature_name="Binning",
            horizontal=h_bin,
            vertical=v_bin,
            log_name="binning",
        )

    def set_decimation(self, h_dec, v_dec) -> bool:
        """设置水平和垂直 Decimation。"""

        return self._set_sampling_pair(
            feature_name="Decimation",
            horizontal=h_dec,
            vertical=v_dec,
            log_name="decimation",
        )

    # ========== trigger ==========
    def _set_and_verify_enum(
            self,
            node_name: str,
            requested_value: int,
    ) -> int:
        """设置一个枚举节点，并读回确认实际值。"""

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                f"相机未打开，无法设置枚举节点 "
                f"{node_name}"
            )

        expected_value = int(requested_value)

        ret = cam.MV_CC_SetEnumValue(
            node_name,
            expected_value,
        )

        if ret != 0:
            raise RuntimeError(
                f"枚举节点设置失败: "
                f"node={node_name}, "
                f"value={expected_value}, "
                f"error=0x{ret:X}"
            )

        # 设置成功后，从同一个节点读回实际值。
        enum_info = MVCC_ENUMVALUE()

        read_ret = cam.MV_CC_GetEnumValue(
            node_name,
            enum_info,
        )

        if read_ret != 0:
            raise RuntimeError(
                f"枚举节点读回失败: "
                f"node={node_name}, "
                f"error=0x{read_ret:X}"
            )

        actual_value = int(enum_info.nCurValue)

        if actual_value != expected_value:
            raise RuntimeError(
                f"枚举节点实际值不一致: "
                f"node={node_name}, "
                f"请求={expected_value}, "
                f"实际={actual_value}"
            )

        return actual_value

    def set_trigger_mode(self, mode="off") -> bool:
        """设置相机触发方式。

        支持：
            off：自由运行；
            software：软件触发；
            hardware：Line0 硬件触发。
        """

        if self._cam is None:
            raise RuntimeError(
                "相机未打开，无法设置触发模式"
            )

        if self._is_grabbing:
            raise RuntimeError(
                "相机正在取流，必须先停止取流"
                "才能修改触发模式"
            )

        # 统一转换成小写字符串，允许传入：
        #
        #   "OFF"
        #   "Software"
        #   " hardware "
        #
        # 但内部最终都使用规范名称。
        normalized_mode = str(mode).strip().lower()

        valid_modes = {
            "off",
            "software",
            "hardware",
        }

        if normalized_mode not in valid_modes:
            raise ValueError(
                f"不支持的触发模式: {mode!r}，"
                f"可选值为 off / software / hardware"
            )

        try:
            if normalized_mode == "off":
                # 自由运行只需要关闭触发模式。
                #
                # TriggerSource 保留原值没有关系，
                # 因为 TriggerMode=OFF 时触发源不参与曝光控制。
                self._set_and_verify_enum(
                    "TriggerMode",
                    MV_TRIGGER_MODE_OFF,
                )

            else:
                # 修改触发来源前，先关闭 TriggerMode。
                self._set_and_verify_enum(
                    "TriggerMode",
                    MV_TRIGGER_MODE_OFF,
                )

                if normalized_mode == "software":
                    trigger_source = (
                        MV_TRIGGER_SOURCE_SOFTWARE
                    )
                else:
                    trigger_source = (
                        MV_TRIGGER_SOURCE_LINE0
                    )

                # 在 TriggerMode=OFF 状态下设置触发来源。
                self._set_and_verify_enum(
                    "TriggerSource",
                    trigger_source,
                )

                # TriggerSource 设置并读回成功后，
                # 最后再开启 TriggerMode。
                self._set_and_verify_enum(
                    "TriggerMode",
                    MV_TRIGGER_MODE_ON,
                )

        except Exception:
            # 设置过程可能已经修改了部分相机状态。
            #
            # 例如 TriggerMode 已经关闭，
            # 但 TriggerSource 设置失败。
            #
            # 此时软件不能继续声称自己知道真实触发模式。
            self._trigger_mode = "unknown"
            raise

        # 只有所有 SDK 设置和读回都成功后，
        # 才更新 Python 侧状态。
        self._trigger_mode = normalized_mode

        logger.info(
            "trigger: %s",
            normalized_mode,
        )
        return True

    def trigger_software(self) -> bool:
        """发送一次软件触发命令。"""

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                "相机未打开，无法执行软件触发"
            )

        # 软件触发必须在已经确认的软件触发模式下执行。
        if self._trigger_mode != "software":
            raise RuntimeError(
                f"当前触发模式不是 software，"
                f"无法执行软件触发: "
                f"current={self._trigger_mode}"
            )

        # 按海康 SDK 的正常使用顺序，
        # 需要先 StartGrabbing，再发送软件触发命令。
        if not self._is_grabbing:
            raise RuntimeError(
                "相机尚未启动取流，"
                "无法执行软件触发"
            )

        ret = cam.MV_CC_SetCommandValue(
            "TriggerSoftware"
        )

        if ret != 0:
            # 触发命令失败时立即报错。
            #
            # 不再继续等待一张实际上不会产生的图片。
            raise RuntimeError(
                f"软件触发命令发送失败: "
                f"error=0x{ret:X}"
            )

        return True

    # ========== sync grab (GetImageBuffer) ==========
    def capture_frame(
            self,
            timeout_ms=500,
    ) -> np.ndarray:
        """通过软件触发采集一张图片。"""

        if self._cam is None:
            raise RuntimeError(
                "相机未打开，请先调用 open()"
            )

        timeout_value = int(timeout_ms)

        if timeout_value <= 0:
            raise ValueError(
                f"取帧超时时间必须大于 0: "
                f"timeout_ms={timeout_value}"
            )

        # capture_frame() 负责一次完整的软件触发采图：
        #
        #   设置软件触发模式
        #       ↓
        #   启动取流
        #       ↓
        #   发送软件触发命令
        #       ↓
        #   等待并获取图像
        #       ↓
        #   停止取流
        self.set_trigger_mode("software")
        self.start_grabbing()

        try:
            # trigger_software() 失败时会立即抛出：
            #
            #   软件触发命令发送失败
            #
            # 不会再继续等待图像。
            self.trigger_software()

            img = self.get_frame(
                timeout_value
            )

            if img is None:
                # 能走到这里，说明软件触发命令已经发送成功，
                # 但在指定时间内没有从 SDK 获得图像。
                raise RuntimeError(
                    f"软件触发已发送，"
                    f"但在 {timeout_value}ms 内"
                    f"没有收到图像"
                )

            return img

        finally:
            # 无论触发失败、取帧超时还是图像转换失败，
            # 都要停止本次取流。
            self.stop_grabbing()

    def start_grabbing(self) -> bool:
        """启动相机取流。

        可以安全地重复调用：
            相机未打开时抛出明确异常；
            相机已经取流时直接返回；
            只有尚未取流时才调用 SDK。
        """

        # 保存本次操作要使用的相机对象。
        cam = self._cam

        # 启动取流属于必须有真实相机句柄的操作。
        #
        # 这里不能像 stop_grabbing() 那样直接返回 True，
        # 因为“没有打开相机”不能算作“成功启动取流”。
        if cam is None:
            raise RuntimeError(
                "相机未打开，无法启动取流，请先调用 open()"
            )

        # 已经处于取流状态时，不重复调用 SDK。
        #
        # 第二次调用只表示：
        # “请确保相机处于取流状态。”
        #
        # 由于相机已经取流，所以这个要求已经满足。
        if self._is_grabbing:
            return True

        ret = cam.MV_CC_StartGrabbing()

        if ret != 0:
            # SDK 启动失败时，不能把状态改成 True。
            #
            # 此时 self._is_grabbing 继续保持 False，
            # 让程序状态与相机实际状态一致。
            raise RuntimeError(
                f"StartGrabbing fail: 0x{ret:X}"
            )

        # 只有 SDK 明确返回成功后，才更新软件侧状态。
        self._is_grabbing = True

        logger.info(
            "grabbing started"
        )
        return True

    def stop_grabbing(self) -> bool:
        """停止相机取流。

        可以安全地重复调用：
            相机未打开时直接返回；
            相机已经停止时直接返回；
            只有正在取流时才真正调用 SDK。
        """

        # 相机句柄尚未创建，或者已经被 close() 销毁。
        if self._cam is None:
            return True

        # 当前并没有取流，不重复调用 SDK。
        if not self._is_grabbing:
            return True

        ret = self._cam.MV_CC_StopGrabbing()

        if ret != 0:
            # 停止失败时暂时保留 _is_grabbing=True，
            # 因为程序不能假装相机已经成功停止。
            raise RuntimeError(
                f"StopGrabbing fail: 0x{ret:X}"
            )

        self._is_grabbing = False
        logger.info(
            "grabbing stopped"
        )
        return True

    def get_frame(self, timeout_ms=200):
        """从 SDK 获取一帧图像，并确保 SDK 缓冲区一定被归还。"""

        # 先保存当前相机对象的局部引用。
        #
        # 这样下面获取图像和释放图像使用的是同一个 SDK 对象，
        # 不会因为 self._cam 后续被修改而前后不一致。
        cam = self._cam

        if cam is None:
            raise RuntimeError("相机未打开，请先调用 open()")

        frame = MV_FRAME_OUT()
        memset(byref(frame), 0, sizeof(frame))

        ret = cam.MV_CC_GetImageBuffer(frame, timeout_ms)

        if ret != 0:
            # 获取超时或者当前没有图像时，没有拿到 SDK 缓冲区，
            # 因此也不需要调用 FreeImageBuffer。
            return None

        try:
            # 将 SDK 缓冲区中的图像复制并转换为 NumPy 图像。
            return _frame_to_numpy(frame)

        finally:
            # 只要 GetImageBuffer 成功，就必须归还 SDK 缓冲区。
            #
            # 即使 _frame_to_numpy() 内部发生异常，
            # finally 中的代码也仍然会执行。
            free_ret = cam.MV_CC_FreeImageBuffer(frame)

            if free_ret != 0:
                logger.warning(
                    "FreeImageBuffer fail: 0x%X",
                    free_ret,
                )

    # ========== async callback (RegisterImageCallBackEx) ==========
    def register_frame_callback(self, callback) -> bool:
        """注册或更新图像回调函数。

        callback(img_bgr) 会在相机 SDK 回调线程中被调用。
        """

        if self._cam is None:
            raise RuntimeError(
                "相机未打开，无法注册图像回调"
            )

        if not callable(callback):
            raise TypeError(
                "图像回调必须是可调用对象"
            )

        # 取流过程中不允许更换上层回调。
        #
        # 否则 SDK 回调线程可能正在读取旧回调，
        # GUI 或 Pipeline 线程同时替换为新回调，
        # 形成难以判断的跨线程竞态。
        if self._is_grabbing:
            raise RuntimeError(
                "相机正在取流，必须先停止取流"
                "才能注册或更新图像回调"
            )

        # C 层回调已经成功注册过时，不需要再次调用 SDK。
        #
        # SDK 注册的 C 回调内部会在每一帧到来时，
        # 动态读取 self._user_callback。
        #
        # 因此粗扫结束后切换到精扫，只需要更新
        # Python 上层处理函数即可。
        if self._cb_func_holder is not None:
            self._user_callback = callback
            return True

        # 第一次注册时，进入真正的 SDK 注册流程。
        return self._register_callback(callback)

    def _register_callback(self, callback) -> bool:
        """首次向 SDK 注册底层 C 图像回调。"""

        cam = self._cam

        if cam is None:
            raise RuntimeError(
                "相机未打开，无法注册 SDK 图像回调"
            )

        def sdk_callback(
                pData,
                pFrameInfo,
                pUser,
        ):
            """由相机 SDK 线程调用的底层回调。"""

            try:
                # SDK 没有提供有效图像地址或帧信息时直接忽略。
                if not pData or not pFrameInfo:
                    return

                frame_info = cast(
                    pFrameInfo,
                    POINTER(MV_FRAME_OUT_INFO_EX),
                ).contents

                # 必须先把 SDK 图像内存复制到 Python 自己的 bytes。
                #
                # sdk_callback() 返回以后，
                # SDK 原始 pData 指向的缓冲区可能立即被复用。
                frame_bytes = string_at(
                    pData,
                    frame_info.nFrameLen,
                )

                raw = np.frombuffer(
                    frame_bytes,
                    dtype=np.uint8,
                )

                if (
                        frame_info.enPixelType
                        == PixelType_Gvsp_Mono8
                ):
                    image = raw.reshape(
                        frame_info.nHeight,
                        frame_info.nWidth,
                    )

                    image = cv2.cvtColor(
                        image,
                        cv2.COLOR_GRAY2BGR,
                    )

                elif (
                        frame_info.enPixelType
                        == PixelType_Gvsp_BGR8_Packed
                ):
                    image = raw.reshape(
                        frame_info.nHeight,
                        frame_info.nWidth,
                        3,
                    )

                else:
                    # 保留当前项目原有兼容逻辑：
                    # 对单通道、单字节格式尝试按照灰度图处理。
                    image = raw.reshape(
                        frame_info.nHeight,
                        frame_info.nWidth,
                    )

                    image = cv2.cvtColor(
                        image,
                        cv2.COLOR_GRAY2BGR,
                    )

            except Exception as e:
                # ctypes 回调中的异常不能继续穿透到 C SDK。
                #
                # 如果 Python 异常越过 C 回调边界，
                # 通常只会看到难以定位的
                # "Exception ignored on calling ctypes callback"。
                logger.warning(
                    "相机回调图像转换失败: %s",
                    e,
                )
                return

            self._stream_count += 1

            # 先保存本帧要调用的 Python 回调。
            user_callback = self._user_callback

            if user_callback is None:
                return

            try:
                user_callback(image)

            except Exception as e:
                # 上层回调失败不能继续穿透到相机 SDK 线程。
                logger.warning(
                    "相机上层回调执行失败: %s",
                    e,
                )

        callback_type = CFUNCTYPE(
            None,
            POINTER(c_ubyte),
            POINTER(MV_FRAME_OUT_INFO_EX),
            c_void_p,
        )

        # 先使用局部变量创建 C 回调。
        #
        # 在 SDK 明确注册成功前，不修改对象的正式状态。
        callback_holder = callback_type(
            sdk_callback
        )

        ret = cam.MV_CC_RegisterImageCallBackEx(
            callback_holder,
            None,
        )

        if ret != 0:
            # 注册失败时：
            #
            #   self._cb_func_holder 仍然是 None；
            #   self._user_callback 也不修改；
            #   下一次调用仍然可以重新注册。
            raise RuntimeError(
                f"RegisterImageCallBackEx fail: "
                f"0x{ret:X}"
            )

        # 只有 SDK 注册成功后，才正式提交对象状态。
        #
        # 顺序很重要：
        #   先保存 Python 上层回调；
        #   再保存 C 回调对象，表示注册已经完成。
        self._user_callback = callback
        self._cb_func_holder = callback_holder

        logger.info(
            "callback registered (Ex)"
        )
        return True


# ── CT 类级插桩 ──
# HikCamera 每个 SDK 往返方法（open/close/set_roi/set_exposure/
# set_trigger_mode/start_grabbing 等）计时入 perf 注册表，单次 ≥100ms
# 打 [CT][慢]。get_frame 本身是阻塞等帧，耗时无诊断意义，排除。
import perf as _perf

_perf.instrument_class(
    HikCamera, "hw.cam", slow_ms=100.0, exclude={"get_frame"}
)
