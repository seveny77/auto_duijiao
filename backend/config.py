# backend/config.py
"""对焦任务参数：GUI 与后端之间的类型化契约。"""

from dataclasses import dataclass, field
from typing import Optional,Callable

from backend.focus_roi import EvaluationRoi

# 预览回调的参数约定：
#
# callback(
#     image,       # NumPy/OpenCV 图像
#     phase,       # calibrate / coarse / fine
#     sequence,    # 当前阶段中的帧序号
#     score,       # 当前帧清晰度得分
# )
PreviewCallback = Callable[
    [object, str, int, float],
    None,
]

@dataclass
class FocusConfig:
    """字段与 verify_ncc_full 的 parser 参数一一对应，默认值与 parser 一致。"""

    action: str = "search"                     # search / calibrate
    mode: str = "real"                         # real / sim
    strategy: str = "ncc"
    template: str = "data/template.json"

    # AI对焦策略参数。
    dl_model: str = "assets/models/ai/best_resnet.pt"
    shot_position_um: Optional[int] = None
    dl_max_abs_delta_um: float = 600.0

    camera_index: int = 0

    search_start_um: int = 9500
    search_span_um: int = 2000
    coarse_step_um: int = 100
    fine_step_um: int = 5
    fine_half_steps: int = 5
    ncc_min_score: float = 0.5

    calibrate_step_um: int = 5
    calibrate_start_um: Optional[int] = None
    calibrate_span_um: Optional[int] = None
    calibrate_images: Optional[str] = None
    calibrate_downsample: Optional[str] = None
    calibrate_factor: Optional[int] = None

    exposure_us: int = 12000
    coarse_exposure_us: int = 0
    gain_db: float = 0.0
    coarse_binning: int = 4
    coarse_downsample: str = "decimation"
    fine_binning: int = 1
    # 标定、粗扫、精扫、最终成像共用的初始工作窗口。
    # 单位为未降采样的传感器像素；宽高同时为 0 表示使用全幅。
    # 第一版固定在传感器中心，不单独配置 OffsetX/OffsetY。
    work_roi_width_px: int = 0
    work_roi_height_px: int = 0
    # 清晰度评价 ROI，坐标相对于相机硬件 ROI 输出图像左上角。
    # None 表示第一张图到达后自动使用整张硬件 ROI 图像。
    evaluation_roi: Optional[EvaluationRoi] = None
    detect_model: str = "assets/models/yolo/best.pt"
    detect_conf: float = 0.5
    roi_fallback_size: int = 300

    save_dir: Optional[str] = None
    save_images: Optional[str] = None
    save_all: bool = False
    flyscan_timeout: float = 600.0
    frame_wait_timeout: float = 60.0
    final_frame_timeout: float = 3.0
    # 新版连续精扫使用软件触发；0 表示处理完上一帧后立即触发下一帧。
    soft_trigger_interval_s: float = 0.0
    soft_trigger_frame_timeout_s: float = 1.0
    soft_trigger_queue_size: int = 2
    continuous_scan_velocity_um_s: float = 50.0
    yes: bool = False

    cancel_event: Optional[object] = None      # 运行期注入：停止开关
    detect_model_obj: Optional[object] = None  # 运行期注入：已加载的 YOLO
    dl_model_obj: Optional[object] = None      # 运行期注入：已经加载和预热的AI对焦模型。
    # 运行期注入：GUI的MotionService持有已连接的运动后端。
    # Pipeline只借用它执行本轮任务，不负责连接或断开。
    motion_backend: Optional[object] = None
    # 运行期注入：GUI的CameraService持有的常驻相机句柄。
    # Pipeline只借用它执行本轮任务，结束后保持打开（省每轮
    # open的~500ms）；为None时Pipeline自行open/close（CLI路径）。
    camera: Optional[object] = None
    # CLI 或无界面运行时保持 None，不产生任何预览开销。
    # image, phase, sequence, score
    preview_callback: Optional[PreviewCallback] = None
    # 0.1 秒代表最多每秒发送 10 张预览图。
    preview_interval_s: float = 0.1
