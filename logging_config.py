# -*- coding: utf-8 -*-
"""自动对焦项目的通用 logging 配置。

职责：

1. 配置根 logger；
2. 给程序安装控制台 Handler；
3. 统一控制台日志格式；
4. 防止重复安装 Handler；
5. 不包含任何 PyQt 代码，GUI 和命令行都可以使用。
"""

import logging
import sys


# 给我们自己创建的控制台 Handler 使用的标记名称。
#
# 这个名称不是 logging 规定的，
# 而是我们项目自己定义的。
_CONSOLE_HANDLER_MARK = (
    "_autofocus_console_handler"
)


def configure_console_logging(
    level: int = logging.INFO,
) -> logging.Handler:
    """安装项目的控制台日志 Handler。

    参数：
        level:
            最低日志级别，默认是 logging.INFO。

    返回：
        已经存在或者本次新建的控制台 Handler。
    """

    # Windows 控制台的默认 stderr 编码可能不是 UTF-8。
    # logging.StreamHandler 默认写 stderr，因此在创建 Handler 前
    # 尽量统一为 UTF-8，避免中文日志出现乱码。
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(
                encoding="utf-8",
            )
        except (AttributeError, OSError, ValueError):
            # 某些打包环境或被重定向的流不允许 reconfigure。
            # 此时继续使用环境提供的原始编码。
            pass

    # logging.getLogger() 不传名称，
    # 得到的是整个 logging 系统最上层的根 logger。
    root_logger = logging.getLogger()

    # 第一层日志级别过滤。
    #
    # 所有模块 logger 默认都会把日志向上传给根 logger。
    # 根 logger 只接收 level 及以上的日志。
    root_logger.setLevel(level)

    # --------------------------------------------------
    # 防止重复安装控制台 Handler
    # --------------------------------------------------
    #
    # configure_console_logging() 可能被多个程序入口调用，
    # 例如：
    #
    #   gui/main.py
    #   backend/cli.py
    #
    # 如果每次调用都创建一个新的 StreamHandler，
    # 同一条日志可能显示两遍。
    for handler in root_logger.handlers:
        already_installed = getattr(
            handler,
            _CONSOLE_HANDLER_MARK,
            False,
        )

        if already_installed:
            # 如果已经安装过，则更新级别后直接复用。
            handler.setLevel(level)
            return handler

    # --------------------------------------------------
    # 创建控制台 Handler
    # --------------------------------------------------
    #
    # sys.stderr 是 logging 的标准控制台输出位置。
    #
    # 在 PyCharm 中，stderr 同样会显示在 Run 控制台中。
    console_handler = logging.StreamHandler(
        sys.stderr
    )

    # 第二层日志级别过滤。
    #
    # 一条日志需要同时通过：
    #
    #   root_logger 的 level
    #   console_handler 的 level
    #
    # 才会最终显示在控制台。
    console_handler.setLevel(level)

    # 给这个 Handler 添加项目自定义标记。
    #
    # Python 对象可以动态添加属性。
    # 下次调用本函数时，通过这个标记识别它。
    setattr(
        console_handler,
        _CONSOLE_HANDLER_MARK,
        True,
    )

    # --------------------------------------------------
    # 创建控制台 Formatter
    # --------------------------------------------------
    #
    # 最终格式类似：
    #
    # 18:24:20 | INFO | camera.camera_adapter | camera opened
    console_formatter = logging.Formatter(
        fmt=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
        datefmt="%H:%M:%S",
    )

    console_handler.setFormatter(
        console_formatter
    )

    # 把控制台 Handler 安装到根 logger。
    root_logger.addHandler(
        console_handler
    )

    return console_handler
