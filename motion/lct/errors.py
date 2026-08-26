# -*- coding: utf-8 -*-
"""LCT运动控制相关异常。"""


class LctError(RuntimeError):
    """所有LCT运动控制异常的基类。"""


class LctConfigurationError(LctError):
    """LCT路径、通道或硬件参数配置错误。"""


class LctLibraryLoadError(LctError):
    """M60或E4O4动态库加载失败。"""


class LctSdkCallError(LctError):
    """厂家SDK函数返回了失败错误码。"""

    def __init__(
        self,
        device: str,
        operation: str,
        error_code: int,
        detail: str = "",
    ):
        self.device = device
        self.operation = operation
        self.error_code = error_code
        self.detail = detail

        message = (
            f"{device}调用失败: "
            f"operation={operation}, "
            f"error_code={error_code}"
        )

        if detail:
            message = f"{message}, detail={detail}"

        super().__init__(message)


class LctStateError(LctError):
    """设备状态不满足当前操作要求。"""


class LctSafetyError(LctError):
    """运动参数或当前位置不满足安全要求。"""