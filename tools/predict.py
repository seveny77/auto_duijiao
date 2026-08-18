"""通用 YOLO 推理脚本 — 支持 .pt / .onnx / .engine"""
import argparse
from pathlib import Path
from ultralytics import YOLO

# ========== 改这一行即可 ==========
MODEL_PATH = "model\\best.engine"
# ==================================
def resolve(path):
    p = Path(path)
    if p.is_absolute():
        return str(p)
    script_dir = Path(__file__).parent.resolve()
    resolved = (script_dir / p).resolve()
    if resolved.exists():
        return str(resolved)
    resolved = Path.cwd() / p
    return str(resolved.resolve())

def main():
    parser = argparse.ArgumentParser(description="YOLO 推理")
    parser.add_argument("--source", "-s", required=True, help="图片/视频路径或摄像头ID")
    parser.add_argument("--model", "-m", default=MODEL_PATH, help="模型路径")
    parser.add_argument("--conf", type=float, default=0.5, help="置信度阈值")
    parser.add_argument("--show", action="store_true", help="实时显示结果")
    parser.add_argument("--save", action="store_true", help="保存结果到 runs/detect/predict")
    args = parser.parse_args()

    model_path = resolve(args.model)
    print(f"模型: {model_path}")
    if not Path(model_path).exists():
        print(f"错误: 模型文件不存在: {model_path}")
        return

    model = YOLO(model_path)
    results = model.predict(
        source=args.source,
        conf=args.conf,
        save=args.save,
        show=args.show,
    )

    for r in results:
        if r.boxes is None:
            print("未检测到目标")
            continue
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf[0])
            print(f"[{conf:.2f}] ({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})")

if __name__ == "__main__":
    main()