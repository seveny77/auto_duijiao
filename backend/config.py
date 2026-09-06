"""连续自动对焦任务的类型化配置。"""

from dataclasses import dataclass
from typing import Callable, Optional

from backend.focus_roi import EvaluationRoi


PreviewCallback = Callable[[object, str, int, float], None]
BestFrameReadyCallback = Callable[[object], None]


@dataclass
class FocusConfig:
    """一轮连续精扫所需的配置与运行期依赖。"""

    mode: str = "real"  # real / sim
    camera_index: int = 0
    search_start_um: int = 9500
    search_span_um: int = 2000
    continuous_scan_velocity_um_s: float = 50.0
    exposure_us: int = 3000
    gain_db: float = 0.0
    # 全分辨率传感器坐标；宽高均为 0 表示全幅。
    work_roi_width_px: int = 0
    work_roi_height_px: int = 0
    camera_decimation: int = 1
    # 相对于硬件 ROI 输出图像左上角；None 表示整张图。
    evaluation_roi: Optional[EvaluationRoi] = None
    continuous_capture_fps: float = 20.0
    continuous_capture_queue_size: int = 30
    continuous_first_frame_timeout_s: float = 1.0
    continuous_drain_timeout_s: float = 5.0
    flyscan_timeout: float = 600.0
    save_dir: Optional[str] = None
    yes: bool = False

    # 由 GUI 在任务提交前注入，不写入 config.json。
    cancel_event: Optional[object] = None
    motion_backend: Optional[object] = None
    camera: Optional[object] = None
    preview_callback: Optional[PreviewCallback] = None
    best_frame_ready_callback: Optional[BestFrameReadyCallback] = None
    preview_interval_s: float = 0.1
