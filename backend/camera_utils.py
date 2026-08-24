import logging
from typing import Optional, Tuple

from backend.constants import (
    SENSOR_H,
    SENSOR_W,
)


logger = logging.getLogger(__name__)


class RoiAlignmentError(ValueError):
    """ROI 对齐或边界校验失败。"""

def resolve_sensor_size(cam) -> Tuple[int, int]:
    """优先读取相机尺寸，读取失败时使用默认兜底值。"""

    getter = getattr(
        cam,
        "get_sensor_size",
        None,
    )

    # 仿真相机或测试FakeCam可能没有get_sensor_size()。
    if not callable(getter):
        logger.info(
            "相机对象不支持尺寸读取，使用默认值: %dx%d",
            SENSOR_W,
            SENSOR_H,
        )
        return SENSOR_W, SENSOR_H

    try:
        width, height = getter()

        width = int(width)
        height = int(height)

        if width <= 0 or height <= 0:
            raise ValueError(
                f"相机返回了无效尺寸: {width}x{height}"
            )

    except Exception as error:
        logger.warning(
            "读取相机尺寸失败，使用默认值: %dx%d，原因: %s",
            SENSOR_W,
            SENSOR_H,
            error,
        )
        return SENSOR_W, SENSOR_H

    logger.info(
        "使用相机自动尺寸: %dx%d",
        width,
        height,
    )

    return width, height

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
) -> Tuple[int, int]:
    """设置目标 Binning 下的全幅图像，并返回传感器尺寸。"""

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

    width = (sensor_width // binning) // 4 * 4
    height = (sensor_height // binning) // 4 * 4

    cam.set_roi(
        0,
        0,
        width,
        height,
    )

    return sensor_width, sensor_height


def set_coarse_frame(
    cam,
    mode: str,
    factor: int,
) -> Tuple[int, int]:
    """设置粗扫降采样，并返回原始传感器尺寸。"""

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

    width = (sensor_width // factor) // 4 * 4
    height = (sensor_height // factor) // 4 * 4

    cam.set_roi(
        0,
        0,
        width,
        height,
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
