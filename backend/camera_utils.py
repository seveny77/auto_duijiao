import logging
from typing import Optional, Tuple

from backend.constants import (
    SENSOR_H,
    SENSOR_W,
)


logger = logging.getLogger(__name__)


class RoiAlignmentError(ValueError):
    """ROI 对齐或边界校验失败。"""


# 5120 能被 32 整除；工作窗口宽高按 32 对齐后，在 2×2、4×4
# 降采样下仍能得到整数且至少 4 像素对齐的尺寸和居中偏移。
WORK_ROI_ALIGNMENT = 32

def resolve_sensor_size(cam=None) -> Tuple[int, int]:
    """返回项目配置的固定相机工作分辨率。

    ``cam`` 参数仅为兼容现有调用点而保留。对焦流程不能使用
    ``WidthMax`` 作为工作画面尺寸：它是相机允许的最大值，可能与 MVS
    中实际设定的 Width 不一致，从而生成相机不接受的 ROI。
    """

    del cam
    logger.info(
        "使用 constants.py 配置的相机尺寸: %dx%d",
        SENSOR_W,
        SENSOR_H,
    )
    return SENSOR_W, SENSOR_H


def resolve_work_roi(
    work_width_px: int = 0,
    work_height_px: int = 0,
    sensor_size: Tuple[int, int] = (SENSOR_W, SENSOR_H),
) -> Tuple[int, int, int, int]:
    """返回全分辨率坐标系中的居中初始工作窗口。

    宽高同时为 0 表示全幅。非零尺寸按 32 像素向下对齐，使同一物理
    窗口可以安全换算到 1×1、2×2 和 4×4 采样阶段。
    """

    sensor_width, sensor_height = map(int, sensor_size)
    width = int(work_width_px)
    height = int(work_height_px)

    if width == 0 and height == 0:
        return 0, 0, sensor_width, sensor_height
    if width <= 0 or height <= 0:
        raise RoiAlignmentError(
            "初始工作窗口宽高必须同时为 0（全幅），或同时大于 0: "
            f"width={width}, height={height}"
        )

    aligned_width = width - width % WORK_ROI_ALIGNMENT
    aligned_height = height - height % WORK_ROI_ALIGNMENT
    if aligned_width != width or aligned_height != height:
        logger.warning(
            "初始工作窗口需 %d 对齐: %dx%d -> %dx%d",
            WORK_ROI_ALIGNMENT,
            width,
            height,
            aligned_width,
            aligned_height,
        )
        width = aligned_width
        height = aligned_height

    if width < WORK_ROI_ALIGNMENT or height < WORK_ROI_ALIGNMENT:
        raise RoiAlignmentError(
            "初始工作窗口对齐后过小: "
            f"{width}x{height}"
        )
    if width > sensor_width or height > sensor_height:
        raise RoiAlignmentError(
            "初始工作窗口超出传感器: "
            f"{width}x{height} > {sensor_width}x{sensor_height}"
        )

    x = (sensor_width - width) // 2
    y = (sensor_height - height) // 2
    return x, y, width, height


def _phase_work_roi(
    sensor_size: Tuple[int, int],
    work_width_px: int,
    work_height_px: int,
    factor: int,
) -> Tuple[int, int, int, int]:
    """把全分辨率初始窗口换算到当前采样倍率的相机坐标系。"""

    if factor <= 0:
        raise RoiAlignmentError(
            f"采样倍率必须大于 0: {factor}"
        )

    sensor_width, sensor_height = sensor_size
    base_x, base_y, base_width, base_height = resolve_work_roi(
        work_width_px,
        work_height_px,
        sensor_size=sensor_size,
    )
    phase_sensor = (
        sensor_width // factor,
        sensor_height // factor,
    )
    phase_roi = (
        base_x // factor,
        base_y // factor,
        base_width // factor,
        base_height // factor,
    )
    return align_window(
        phase_roi,
        sensor_size=phase_sensor,
        inc=4,
    )

def align_window(
    roi: Tuple[int, int, int, int],
    sensor_size: Tuple[int, int] = (
        SENSOR_W,
        SENSOR_H,
    ),
    inc: int = 4,
) -> Tuple[int, int, int, int]:
    """将硬件 ROI 向下对齐，并检查是否超出传感器。"""

    if inc <= 0:
        raise RoiAlignmentError(
            f"ROI 对齐增量必须大于 0: {inc}"
        )

    x, y, width, height = roi

    if any(
        value % inc
        for value in (
            x,
            y,
            width,
            height,
        )
    ):
        aligned_x = x - x % inc
        aligned_y = y - y % inc
        aligned_width = width - width % inc
        aligned_height = height - height % inc

        logger.warning(
            "开窗参数需 %d 对齐: "
            "(%d,%d,%d,%d) -> "
            "(%d,%d,%d,%d)",
            inc,
            x,
            y,
            width,
            height,
            aligned_x,
            aligned_y,
            aligned_width,
            aligned_height,
        )

        x = aligned_x
        y = aligned_y
        width = aligned_width
        height = aligned_height

    sensor_width, sensor_height = sensor_size

    if (
        width < inc
        or height < inc
        or x < 0
        or y < 0
        or x + width > sensor_width
        or y + height > sensor_height
    ):
        raise RoiAlignmentError(
            "开窗越界: "
            f"({x},{y},{width},{height}) "
            f"超出传感器 "
            f"{sensor_width}x{sensor_height}"
        )

    return (
        x,
        y,
        width,
        height,
    )


def set_full_frame(
    cam,
    binning: int,
    work_width_px: int = 0,
    work_height_px: int = 0,
) -> Tuple[int, int]:
    """设置目标 Binning 下的初始工作窗口，并返回传感器尺寸。"""

    # 相机可能记住上一次运行的采样设置。
    # 先恢复到1×1，才能读取原始传感器尺寸。
    cam.set_binning(1, 1)
    cam.set_decimation(1, 1)

    sensor_width, sensor_height = (
        resolve_sensor_size(cam)
    )

    # 先恢复到原始传感器全幅。
    cam.set_roi(
        0,
        0,
        sensor_width,
        sensor_height,
    )

    # 再应用本次需要的Binning。
    cam.set_binning(
        binning,
        binning,
    )

    roi = _phase_work_roi(
        (sensor_width, sensor_height),
        work_width_px,
        work_height_px,
        binning,
    )

    cam.set_roi(*roi)
    logger.info(
        "当前阶段使用初始工作窗口: (%d,%d) %dx%d，采样倍率=%d",
        *roi,
        binning,
    )

    return sensor_width, sensor_height


def set_coarse_frame(
    cam,
    mode: str,
    factor: int,
    work_width_px: int = 0,
    work_height_px: int = 0,
) -> Tuple[int, int]:
    """在初始工作窗口基础上设置粗扫降采样。"""

    cam.set_binning(1, 1)
    cam.set_decimation(1, 1)

    sensor_width, sensor_height = resolve_sensor_size(cam)

    cam.set_roi(
        0,
        0,
        sensor_width,
        sensor_height,
    )

    if mode == "decimation":
        cam.set_decimation(
            factor,
            factor,
        )
    else:
        cam.set_binning(
            factor,
            factor,
        )

    roi = _phase_work_roi(
        (sensor_width, sensor_height),
        work_width_px,
        work_height_px,
        factor,
    )

    cam.set_roi(*roi)
    logger.info(
        "粗扫使用初始工作窗口: (%d,%d) %dx%d，%s=%d",
        *roi,
        mode,
        factor,
    )

    return sensor_width, sensor_height


def box_to_roi(
    x1,
    y1,
    x2,
    y2,
    binning,
    sensor_size: Optional[Tuple[int, int]] = None,
):
    """把降采样图像中的检测框转换成传感器 ROI。"""

    if sensor_size is None:
        sensor_size = (
            SENSOR_W,
            SENSOR_H,
        )

    roi = (
        int(round(x1 * binning)),
        int(round(y1 * binning)),
        int(round((x2 - x1) * binning)),
        int(round((y2 - y1) * binning)),
    )

    return align_window(
        roi,
        sensor_size=sensor_size,
    )


def fallback_roi(
    size,
    sensor_size: Optional[Tuple[int, int]] = None,
):
    """返回指定传感器中央的固定大小 ROI。"""

    if sensor_size is None:
        sensor_size = (
            SENSOR_W,
            SENSOR_H,
        )

    sensor_width, sensor_height = sensor_size

    x = (sensor_width - size) // 2
    y = (sensor_height - size) // 2

    return align_window(
        (
            x,
            y,
            size,
            size,
        ),
        sensor_size=sensor_size,
    )


def frame_positions(
    n: int,
    start_um: int,
    step_um: int,
) -> list[float]:
    """返回 n 帧的位置；第 k 帧为 start + (k + 1) * step。"""

    return [
        start_um + step_um * (index + 1)
        for index in range(n)
    ]
