# -*- coding: utf-8 -*-
"""程序入口（任务 1）"""

import os
import sys
from PyQt5.QtWidgets import QApplication

# 无论从哪个目录/以何种方式启动，都把项目根加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)   # ① 创建"总管"（唯一）
    window = MainWindow()          # ② 创建主窗口实例
    window.show()                  # ③ 把窗口显示出来
    sys.exit(app.exec_())          # ④ 进入事件循环，窗口关闭后退出程序


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
