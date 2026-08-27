# -*- coding: utf-8 -*-
"""凌臣M60和E4O4运动控制后端。"""

from motion.lct.config import (
    LctMotionConfig,
    ScanDirection,
)
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
from motion.lct.e4o4_api import (
    E4O4Api,
    E4O4EncoderConfig,
    E4O4LineCompareConfig,
    E4O4PreCompareConfig,
    E4O4SlaveResource,
    E4O4TriggerConfig,
)
from motion.state import MotionState


__all__ = [
    "LctMotionConfig",
    "LctMotionBackend",
    "ScanDirection",
    "E4O4Api",
    "E4O4EncoderConfig",
    "E4O4LineCompareConfig",
    "E4O4PreCompareConfig",
    "E4O4SlaveResource",
    "E4O4TriggerConfig",
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
