# -*- coding: utf-8 -*-
"""自动对焦运动后端的公共接口。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ContinuousScanResult:
    start_um: int
    end_um: int
    actual_end_um: float
    velocity_um_s: float
    motion_elapsed_ms: float


class MotionBackend(ABC):
    """当前连续采集自动对焦所需的 M60 运动能力。"""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        pass

    @abstractmethod
    def connect(self) -> None:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass

    @abstractmethod
    def prepare_new_task(self) -> None:
        pass

    @abstractmethod
    def read_stroke_range(self) -> Tuple[int, int]:
        pass

    @abstractmethod
    def continuous_scan(
        self,
        start_um: int,
        end_um: int,
        timeout_s: float,
        cancel_event=None,
        velocity_um_s: Optional[float] = None,
    ) -> ContinuousScanResult:
        """从起点连续运动到终点，不配置步距或硬件触发。"""

    @abstractmethod
    def move_to_position(self, position_um: int, timeout_s: float, cancel_event=None):
        pass

    @abstractmethod
    def get_state(self):
        pass

    @abstractmethod
    def is_ready_for_autofocus(self) -> bool:
        pass

    @abstractmethod
    def clear_alarm(self):
        pass

    @abstractmethod
    def servo_on(self):
        pass

    @abstractmethod
    def servo_off(self):
        pass

    @abstractmethod
    def home(self, cancel_event=None, timeout_s=None):
        pass

    @abstractmethod
    def cancel_current_motion(self) -> None:
        pass
