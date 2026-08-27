# -*- coding: utf-8 -*-
"""LCT组合后端只连接验收：不使能、不回零、不运动。"""

import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui.app.services.config_service import DEFAULT_MOTION_CONFIG
from motion.lct import LctMotionBackend, LctMotionConfig


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = LctMotionConfig(**DEFAULT_MOTION_CONFIG)
    backend = LctMotionBackend(config)

    print("[SAFE] 本次只连接M60/E4O4并读取行程，不执行运动")
    try:
        backend.connect()
        print(f"[OK] backend={backend.backend_name}")
        print(f"[OK] connected={backend.is_connected()}")
        print(f"[OK] stroke_um={backend.read_stroke_range()}")
        return 0
    finally:
        backend.disconnect()
        print("[CLEANUP] 运动后端已安全断开")


if __name__ == "__main__":
    raise SystemExit(main())
