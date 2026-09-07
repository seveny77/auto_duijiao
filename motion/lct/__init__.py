# -*- coding: utf-8 -*-
"""凌臣 M60 单轴运动控制后端。"""

from motion.lct.config import LctMotionConfig
from motion.lct.errors import (
    LctConfigurationError,
    LctError,
    LctLibraryLoadError,
    LctSafetyError,
    LctSdkCallError,
    LctStateError,
)
from motion.lct.m60_api import (
    M60Api,
    M60AxisStatus,
    M60HomingParameters,
    M60SlaveResource,
)
from motion.lct.backend import LctMotionBackend
from motion.state import MotionState


__all__ = [
    "LctMotionConfig",
    "LctMotionBackend",
    "M60Api",
    "M60AxisStatus",
    "M60HomingParameters",
    "LctError",
    "LctConfigurationError",
    "LctLibraryLoadError",
    "LctSdkCallError",
    "LctStateError",
    "LctSafetyError",
    "M60SlaveResource",
    "MotionState",
]
