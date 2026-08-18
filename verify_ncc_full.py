# verify_ncc_full.py —— 薄壳：所有实现已迁到 backend/，此处仅为向后兼容
"""向后兼容层：旧代码 from verify_ncc_full import ... 仍然可用。"""

from backend.cli import build_parser, main
from backend.pipeline import run_search, run_calibrate
from backend.config import FocusConfig
from backend.result import SearchResult, CalibrateResult
from backend.collector import PhaseCollector, save_jpg, save_phase_images
from backend.camera_utils import (
    set_full_frame, set_coarse_frame, box_to_roi, fallback_roi, frame_positions,
)
from backend.constants import SENSOR_W, SENSOR_H
from backend.ncc import ncc_predict_peak
from backend.detection import detect_roi


if __name__ == "__main__":
    raise SystemExit(main())
