# -*- coding: utf-8 -*-
"""自动对焦运动后端的公共接口。"""

from abc import ABC, abstractmethod
from typing import Tuple


class MotionBackend(ABC):
    """自动对焦流程所依赖的运动控制接口。

    上层 Pipeline 只表达“要完成什么动作”，不关心底层具体由
    PLC、M60轴卡还是E4O4位置比较器完成。
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """返回运动后端名称，例如 plc 或 lct。"""

    @abstractmethod
    def connect(self) -> None:
        """连接运动控制设备并完成必要的初始化。"""

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

        主要用于：

        - NCC标定；
        - 粗扫；
        - 精扫。

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
            硬件实际输出的相机触发次数。

        注意：
            对M60/E4O4后端，该方法由E4O4线性比较器实现。

            当前约定第一个拍照位置为：

                start_um + step_um

            最后一个拍照位置为：

                end_um
        """

    @abstractmethod
    def capture_at_position(
        self,
        position_um: int,
        timeout_s: float,
    ) -> int:
        """在指定物理位置获取一张最终图像。

        Args:
            position_um:
                最终图像的目标焦点位置，单位为微米。

            timeout_s:
                本次动作允许的最大执行时间。

        Returns:
            硬件实际输出的相机触发次数，正常应当为1。

        后端实现约定：

        - PLC后端暂时继续使用现有定点拍照流程；
        - M60/E4O4后端使用单点预设定比较器飞拍；
        - 上层不再传递“最佳帧序号”，而是传递物理位置。
        """

    def finish_cycle(self) -> None:
        """通知运动后端本轮自动对焦已经结束。

        PLC后端需要写入流程完成寄存器。

        M60/E4O4后端目前不需要额外通知，因此可以保留默认空实现。
        """
