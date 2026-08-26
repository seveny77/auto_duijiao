# -*- coding: utf-8 -*-
"""自动对焦运动后端的公共接口。"""

from abc import ABC, abstractmethod
from typing import Tuple


class MotionBackend(ABC):
    """自动对焦流程所依赖的运动控制接口。

    上层Pipeline只表达需要完成的运动和拍照动作，不关心底层
    具体由真实LCT轴卡还是仿真运动后端完成。
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """返回运动后端名称，例如lct或sim。"""

    @abstractmethod
    def connect(self) -> None:
        """连接运动控制设备并完成必要初始化。"""

    @abstractmethod
    def disconnect(self) -> None:
        """安全停止并释放运动控制设备。"""

    @abstractmethod
    def is_connected(self) -> bool:
        """返回运动控制设备当前是否已经连接。"""

    @abstractmethod
    def read_stroke_range(self) -> Tuple[int, int]:
        """读取软件允许行程。

        Returns:
            (minimum_um, maximum_um)，单位均为微米。
        """

    @abstractmethod
    def linear_fly_scan(
        self,
        start_um: int,
        end_um: int,
        step_um: int,
        timeout_s: float,
    ) -> int:
        """执行等间距飞拍。

        主要用于NCC标定、粗扫和精扫。

        Args:
            start_um:
                运动起点，单位为微米。

            end_um:
                运动终点，单位为微米。

            step_um:
                相邻触发位置之间的物理距离，单位为微米。

            timeout_s:
                本次飞拍允许的最大执行时间。

        Returns:
            E4O4实际输出的相机触发次数。

        约定：
            第一个拍照位置为start_um + step_um，
            最后一个拍照位置为end_um。
        """

    @abstractmethod
    def capture_at_position(
        self,
        position_um: int,
        timeout_s: float,
    ) -> int:
        """在指定物理位置执行一次最终单点飞拍。

        Args:
            position_um:
                最终图像的目标焦点位置，单位为微米。

            timeout_s:
                本次动作允许的最大执行时间。

        Returns:
            E4O4实际输出的相机触发次数，正常应当为1。

        实现约定：
            M60先移动到目标位置前的准备位置，然后以设定速度经过
            position_um；E4O4使用单点预设定比较器触发相机。
        """