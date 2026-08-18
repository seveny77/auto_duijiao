import argparse
import sys
from backend.config import FocusConfig
from backend.pipeline import run_search,run_calibrate
# ============================================================
# CLI / 主流程
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="全扫标定 + NCC 搜索 + 小区间精扫")
    p.add_argument("--strategy", default="ncc", help="搜索策略: ncc / dummy")
    p.add_argument("--mode", choices=["real", "sim"], default="real")
    p.add_argument("--action", choices=["calibrate", "search"], default="search",
                   help="calibrate=全扫标定生成模板；search=加载模板做粗扫/NCC/精扫")

    # 标定专用
    p.add_argument("--calibrate-step-um", type=int, default=5)
    p.add_argument("--calibrate-start-um", type=int, default=None, help="默认=--search-start-um")
    p.add_argument("--calibrate-span-um", type=int, default=None, help="默认=--search-span-um")
    p.add_argument("--calibrate-images", default=None, help="离线标定：图片目录（不连 PLC）")
    p.add_argument("--template", default="data/template.json", help="FocusTemplate JSON 路径")# 标定模板
    # 搜索专用
    p.add_argument("--fine-half-steps", type=int, default=5, help="精扫区间 = 预测峰 ± N×fine_step")
    p.add_argument("--ncc-min-score", type=float, default=0.5, help="NCC 质量门阈值，低于则降级宽窗口")
    # PLC链接参数
    p.add_argument("--plc-host", default="192.168.100.88")
    p.add_argument("--plc-port", type=int, default=502)
    p.add_argument("--camera-index", type=int, default=0)
    # 拍照位设置、步长
    p.add_argument("--search-start-um", type=int, default=9500)#起始位置
    p.add_argument("--search-span-um", type=int, default=2000)  #遍历区间
    p.add_argument("--coarse-step-um", type=int, default=100)   #遍历步长
    p.add_argument("--fine-step-um", type=int, default=5)
    # 相机参数设置
    p.add_argument("--exposure-us", type=int, default=12000)
    p.add_argument("--coarse-exposure-us", type=int, default=0,
                   help="粗扫曝光（默认 0 = 自动 = 精扫曝光 ÷ binning²）")
    p.add_argument("--gain-db", type=float, default=0.0)
    p.add_argument("--coarse-binning", type=int, default=4)
    p.add_argument("--coarse-downsample", choices=["decimation", "binning"], default="decimation",
                   help="粗扫降采样方式：decimation 亮度不变（推荐），binning 求和会变亮")
    p.add_argument("--fine-binning", type=int, default=1)
    p.add_argument(
        "--detect-model",
        default=r"F:\项目\自动对焦\code\detect\runs\detect\autofocus\weights\best.pt",
        help="YOLO 模型路径（缺失则降级居中 ROI）",
    )
    p.add_argument("--detect-conf", type=float, default=0.5)
    p.add_argument("--roi-fallback-size", type=int, default=700)
    p.add_argument("--save-dir", default=None, help="保存粗扫/精扫/定拍图")
    p.add_argument("--save-images", default=None,
                   help="把本次扫描的全部帧存为 jpg 到该目录（文件名含序号和实际位置µm）")
    p.add_argument("--save-all", action="store_true",
                  help="保存全部评价图像（粗扫 img_0000..，精扫 img_0100..）")
    p.add_argument("--flyscan-timeout", type=float, default=600.0)
    p.add_argument("--frame-wait-timeout", type=float, default=60.0)
    p.add_argument("--final-frame-timeout", type=float, default=3.0)
    p.add_argument("--yes", action="store_true", help="跳过飞拍确认")
    p.add_argument("--calibrate-downsample", choices=["decimation", "binning"], default=None,
                   help="标定降采样方式（默认跟随粗扫）")
    p.add_argument("--calibrate-factor", type=int, default=None,
                   help="标定降采样倍数（默认跟随粗扫）")
    return p



def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    cfg = FocusConfig(**vars(args))        # ★ parser Namespace → dataclass
    if cfg.action == "calibrate":
        return run_calibrate(cfg)
    return run_search(cfg)
