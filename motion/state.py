# -*- coding: utf-8 -*-
"""运动控制器对GUI和Pipeline暴露的统一状态快照。"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MotionState:
    connected: bool = False
    servo_enabled: bool = False
    homed: bool = False
    position_um: Optional[float] = None
    stroke_min_um: Optional[int] = None
    stroke_max_um: Optional[int] = None
    alarm: bool = False
    emergency_stop: bool = False
    positive_limit: bool = False
    negative_limit: bool = False
    moving: bool = False
    offline: bool = False
    ready_for_autofocus: bool = False
    operation: str = "disconnected"
    message: str = "未连接"
