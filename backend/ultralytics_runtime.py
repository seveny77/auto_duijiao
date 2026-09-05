# -*- coding: utf-8 -*-
"""Ultralytics 在 Windows 工控机上的运行时兼容设置。"""

import os
import logging
from typing import Optional


DEVICE_ENVIRONMENT_VARIABLE = "AUTOFOCUS_YOLO_DEVICE"
logger = logging.getLogger(__name__)


def load_yolo_class():
    """延迟导入 YOLO，并禁止推理过程中启动联网统计线程。"""

    from ultralytics import SETTINGS, YOLO

    # sync=False 是 Ultralytics 公开的持久设置。events 对象已在包导入时
    # 创建，因此还要立即关闭本进程中的实例，确保首次 predict 也不发请求。
    try:
        SETTINGS.update({"sync": False})
    except OSError:
        # 服务账户的 Ultralytics 配置目录可能只读；下方本进程开关仍能
        # 确保检测期间不会创建联网线程。
        logger.warning("Ultralytics sync 设置无法持久化，已仅对本进程禁用")
    from ultralytics.utils.events import events

    events.enabled = False
    events.events.clear()
    return YOLO


def resolve_yolo_device() -> Optional[str]:
    """返回显式设备；None 让 Ultralytics 使用正常的自动选择逻辑。

    检测服务已将 CUDA 模型生命周期和推理移到专用 CPython 线程，避免
    Windows Qt 原生工作线程中的线程状态问题，因此 Python 3.13 也可使用
    Ultralytics 的正常 CUDA 自动选择。环境变量仍可覆盖为 ``cpu``、``0`` 等。
    """

    configured = os.environ.get(DEVICE_ENVIRONMENT_VARIABLE)
    if configured is not None and configured.strip():
        return configured.strip()
    return None


def device_predict_kwargs(device: Optional[str]) -> dict:
    """只在需要时传 device，保持第三方模型工厂和旧平台行为兼容。"""

    return {"device": device} if device else {}
