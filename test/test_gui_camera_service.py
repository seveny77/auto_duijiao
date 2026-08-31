# -*- coding: utf-8 -*-
"""GUI相机常驻句柄服务冒烟测试。

验证内容（全部只读断言 + 一次真实相机连接/断开往返）：
1. 相机组新增“连接相机”按钮与状态标签，且在lock_widgets清单内
   （任务期间禁止断开句柄）；
2. CameraService完成装配（main_window注入camera_fn/shutdown服务）；
3. 真实硬件上点击“连接”→后台线程open→句柄交接→按钮/标签翻转；
4. 再次点击→disconnect→句柄释放→状态复位；
5. 窗口closeEvent安全走完关闭序列。

用法：
    python app/test/test_gui_camera_service.py            # 有相机则连/断一次
    python app/test/test_gui_camera_service.py --no-hw    # 仅装配断言
"""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication


CHECKS = []


def check(name: str, ok: bool, detail: str = ""):
    CHECKS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def wait_condition(app, timeout_s: float, predicate):
    """让Qt事件循环转起来等条件成立（QueuedConnection信号需要它）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return predicate()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-hw",
        action="store_true",
        help="只做装配断言，不连接真实相机",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)

    from gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    app.processEvents()

    panel = window.param_panel
    service = window.camera_service

    # ---------- 1. 面板控件 ----------
    check(
        "按钮初始文案",
        panel.camera_connect_btn.text() == "连接相机",
        panel.camera_connect_btn.text(),
    )
    check(
        "标签初始文案",
        panel.camera_connection_label.text() == "未连接",
        panel.camera_connection_label.text(),
    )
    check(
        "按钮在lock_widgets清单内",
        panel.camera_connect_btn in panel.lock_widgets(),
    )

    # ---------- 2. 服务装配 ----------
    check("CameraService已创建", service is not None)
    check("初始未连接", service.is_connected is False)
    check(
        "FocusRunService拿到camera_fn",
        window.focus_run_service._camera_fn() is None,
        "未连接时应返回None",
    )
    check(
        "LiveViewService拿到camera_fn",
        window.live_view_service._camera_fn() is None,
    )
    check(
        "ShutdownService订阅相机服务",
        window.shutdown_service._camera_service is service,
    )

    if args.no_hw:
        window.close()
        check("窗口关闭", not window.isVisible())
        return summarize()

    # ---------- 3. 真实连接往返 ----------
    print("[INFO] 点击“连接相机”（后台open，最长等15s）...")
    service.connect(0)
    connected = wait_condition(
        app, 15.0, lambda: service._connect_thread is None
    )
    check("连接流程收尾", connected)

    if service.is_connected:
        check("is_connected为真", True)
        check(
            "按钮翻转为断开",
            panel.camera_connect_btn.text() == "断开相机",
            panel.camera_connect_btn.text(),
        )
        check(
            "标签翻转为已连接",
            panel.camera_connection_label.text() == "已连接",
            panel.camera_connection_label.text(),
        )
        check(
            "camera_fn返回常驻句柄",
            window.focus_run_service._camera_fn() is service.camera,
        )

        print("[INFO] 点击“断开相机”...")
        service.disconnect()
        app.processEvents()
        check("断开后is_connected为假", service.is_connected is False)
        check(
            "按钮恢复连接",
            panel.camera_connect_btn.text() == "连接相机",
            panel.camera_connect_btn.text(),
        )
    else:
        check(
            "无相机时进入失败态（按钮/标签复位）",
            panel.camera_connect_btn.text() == "连接相机"
            and panel.camera_connect_btn.isEnabled(),
            "当前环境未连上相机，按失败路径断言",
        )

    # ---------- 4. 关闭序列 ----------
    window.close()
    app.processEvents()
    check("窗口关闭", not window.isVisible())
    check("关闭后句柄已释放", service.camera is None)

    return summarize()


def summarize() -> int:
    failed = [name for name, ok, _ in CHECKS if not ok]
    print(
        f"\n[SMOKE] {len(CHECKS) - len(failed)}/{len(CHECKS)} 项通过"
        + (f"，失败: {failed}" if failed else ""),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
