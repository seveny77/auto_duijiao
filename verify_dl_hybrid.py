from verify_ncc_full import frame_positions, run_phase, PhaseCollector, detect_roi, save_jpg,_get_detect_model, \
    set_full_frame, set_coarse_frame
from train_dlfocus import DLDistanceModel
import argparse
import sys
import os
import time
from backend.constants import SENSOR_W, SENSOR_H

def fine_scan_params(predicted_peak_um, half_points=10, step_um=5):
    """计算精扫飞拍参数。
    返回 (start_um, n_frames, positions_um)：
      - start_um: 传给 PLC 的飞拍起点（含尾不含首，需回让一个 step）
      - n_frames: 帧数 = 2*half_points + 1
      - positions_um: 实际采样位置列表，应覆盖 predicted_peak ± half_points*step_um
    """
    half_um = half_points * step_um
    start_um = int(round(predicted_peak_um - half_um - step_um)) # 回让一个 step（含尾不含首）
    n_frames = 2 * half_points + 1
    positions_um = frame_positions(n_frames, start_um, step_um)  # 复用现成的
    return start_um, n_frames, positions_um

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="深度学习预测 + 小区间精扫")
    # 搜索专用

    p.add_argument("--shot-position-um", type=int, default=15001, help="定拍位置（默认 = 行程中心，即 start + span/2）")
    p.add_argument("--search-start-um", type=int, default=14700, help="搜索行程起点(μm)")
    p.add_argument("--search-span-um", type=int, default=600, help="搜索行程跨度(μm)")
    p.add_argument("--fine-half-points", type=int, default=5, help="精扫区间 = 预测峰 ± N×fine_step")
    # PLC链接参数
    p.add_argument("--plc-host", default="192.168.100.88")
    p.add_argument("--plc-port", type=int, default=502)
    p.add_argument("--camera-index", type=int, default=0)
    # 拍照位设置、步长
    p.add_argument("--label-scale", type=int, default=600)  #遍历区间
    p.add_argument("--fine-step-um", type=int, default=5)
    # 相机参数设置
    p.add_argument("--exposure-us", type=int, default=700)
    p.add_argument("--gain-db", type=float, default=0.0)
    p.add_argument("--decimation", type=int, default=4)

    p.add_argument(
        "--detect-model",
        default=r"F:\项目\自动对焦\code\detect\runs\detect\autofocus\weights\best.pt",
        help="YOLO 模型路径（缺失则降级居中 ROI）",
    )
    p.add_argument(
        "--model",
        default=r"F:\项目\自动对焦\code\ct-roi\dlfocus_out/best_resnet.pt",
        help="回归模型路径",
    )
    p.add_argument("--detect-conf", type=float, default=0.5)
    p.add_argument("--roi-fallback-size", type=int, default=700)
    p.add_argument("--save-dir", default=None, help="保存定拍/精扫/定拍图")
    p.add_argument("--save-images", default=None,
                   help="把本次扫描的全部帧存为 jpg 到该目录（文件名含序号和实际位置µm）")
    p.add_argument("--save-all", action="store_true",
                  help="保存全部评价图像（定拍/精扫）")
    p.add_argument("--flyscan-timeout", type=float, default=600.0)
    p.add_argument("--frame-wait-timeout", type=float, default=60.0)
    p.add_argument("--final-frame-timeout", type=float, default=3.0)
    p.add_argument("--yes", action="store_true", help="跳过飞拍确认")

    return p

def print_ct(ct: dict):
    """打印各阶段 CT（ms）。初始化单独列，主流程从定拍触发算到最终取图。"""
    print("\n===== CT 统计 (ms) =====")
    order = ["init_plc_ms", "init_cam_ms", "init_yolo_ms", "init_dl_ms",
             "stage1_capture_ms", "stage2_infer_ms", "stage3_roi_ms",
             "stage4_switch_ms", "stage4_flyscan_ms", "stage4_plc_ms",
             "stage4_eval_ms", "stage5_final_ms", "stage5_move_ms",
             "stage5_waitframe_ms", "main_total_ms"]
    init_total = 0.0
    for k in order:
        if k in ct:
            print(f"  {k:<22} {ct[k]:>9.1f}")
            if k.startswith("init_"):
                init_total += ct[k]
    if "init_total_ms" in ct:
        print(f"  {'init_total_ms':<22} {ct['init_total_ms']:>9.1f}")
    print(f"  {'（初始化合计，不含主流程）':<22}")
    print("=========================")

def run_dl_hybrid(args):
    #硬件初始化
    plc = cam = None
    ct = {}
    try:
        from plc.client import PlcClient
        from camera import HikCamera

        # ── 初始化（单独统计，不并入主流程 CT）──
        t0 = time.perf_counter()
        plc = PlcClient(args.plc_host, args.plc_port, timeout=5.0)
        plc.connect()
        ct["init_plc_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        cam = HikCamera(args.camera_index)
        cam.open()
        ct["init_cam_ms"] = (time.perf_counter() - t0) * 1000

        # 模型初始化
        if not os.path.exists(args.detect_model):
            print(f"[警告] YOLO 模型不存在: {args.detect_model}")
            _detect_model = None
            return {"rc": 1, "error": "YOLO 模型不存在: ..."}
        t0 = time.perf_counter()
        _detect_model = _get_detect_model(args.detect_model)
        ct["init_yolo_ms"] = (time.perf_counter() - t0) * 1000
        print(f"YOLO 模型已加载（主线程）: {args.detect_model}")
        #模型预热
        import numpy as np
        dummy = np.zeros((748, 1024, 3), dtype=np.uint8)

        t0 = time.perf_counter()
        _detect_model.predict(source=dummy, conf=args.detect_conf, verbose=False)
        _detect_model.predict(source=dummy, conf=args.detect_conf, verbose=False)
        ct["init_yolo_warmup_ms"] = (time.perf_counter() - t0) * 1000



        # 评价器初始化
        from adapters.evaluator_opencv import OpenCVSharpnessEvaluator
        evaluator = OpenCVSharpnessEvaluator()

        t0 = time.perf_counter()
        model = DLDistanceModel(args.model)
        # DL 预热（不计入主流程 CT）
        dummy_img = np.zeros((748, 1024, 3), dtype=np.uint8)
        model.predict_frame(dummy_img)
        model.predict_frame(dummy_img)
        ct["init_dl_ms"] = (time.perf_counter() - t0) * 1000
        ct["init_total_ms"] = ct.get("init_plc_ms", 0) + ct.get("init_cam_ms", 0) \
            + ct.get("init_yolo_ms", 0) + ct.get("init_dl_ms", 0)

        # ── 主流程起点：定拍（相机触发）──
        t_main = time.perf_counter()

        # 阶段①：定拍（相机配置 + 软触发取帧）
        t0 = time.perf_counter()
        cam.set_exposure(args.exposure_us)
        cam.set_gain(args.gain_db)
        set_coarse_frame(cam, "decimation", args.decimation)
        img = cam.capture_frame()
        ct["stage1_capture_ms"] = (time.perf_counter() - t0) * 1000

        # 阶段②：DL 推理 + 位置换算
        t0 = time.perf_counter()
        deltaZ = model.predict_frame(img)
        shot = args.shot_position_um if args.shot_position_um is not None else args.search_start_um + args.search_span_um // 2
        P = shot + deltaZ
        P = max(args.search_start_um, min(args.search_start_um + args.search_span_um, P))
        ct["stage2_infer_ms"] = (time.perf_counter() - t0) * 1000

        # 阶段③：YOLO 定 ROI
        t0 = time.perf_counter()
        roi, roi_src, detect_box = detect_roi(
            img, args.detect_model, args.detect_conf, args.decimation,
            args.roi_fallback_size, model=_detect_model,
        )
        ct["stage3_roi_ms"] = (time.perf_counter() - t0) * 1000
        print(f"ROI: {roi}（来源: {roi_src}）")

        # 阶段④：精扫飞拍
        t0 = time.perf_counter()
        set_full_frame(cam, 1)
        cam.set_roi(*roi)
        cam.set_exposure(args.exposure_us)
        ct["stage4_switch_ms"] = (time.perf_counter() - t0) * 1000
        start, n, positions = fine_scan_params(P, args.fine_half_points, args.fine_step_um)
        t0 = time.perf_counter()
        col_f, count_f, dur_f = run_phase(
            plc, cam, evaluator,
            start, start + n * args.fine_step_um, args.fine_step_um, n,
            save_dir=None, start_index=100,  # ← 补这两行
            flyscan_timeout=args.flyscan_timeout,
            wait_timeout=args.frame_wait_timeout,
        )
        ct["stage4_flyscan_ms"] = (time.perf_counter() - t0) * 1000
        ct["stage4_plc_ms"] = dur_f * 1000  # PLC 运动+采图（run_phase 返回）
        ct["stage4_eval_ms"] = ct["stage4_flyscan_ms"] - dur_f * 1000  # 收帧+评价+稳定等待
        scores_map = col_f.scores()
        scores = [scores_map.get(i) for i in range(count_f)]
        if any(s is None for s in scores):
            raise RuntimeError("精扫缺帧")
        best_f = max(range(count_f), key=lambda i: scores[i])
        if best_f == 0 or best_f == count_f-1:
            print("窗口可能没包住真峰")

        # 阶段5，定拍
        t0 = time.perf_counter()
        col_f.stop()
        set_full_frame(cam, 1)
        final_col = PhaseCollector(cam, evaluator, save_dir=args.save_dir, start_index=200)
        final_col.start()
        t0_move = time.perf_counter()
        plc.move_to_position(best_f + 1)
        ct["stage5_move_ms"] = (time.perf_counter() - t0_move) * 1000  # PLC 移动+确认
        plc.process_complete()
        t0_wait = time.perf_counter()
        deadline = time.monotonic() + args.final_frame_timeout
        final_img = None
        while time.monotonic() < deadline:
            if final_col.processed >= 1:
                break
            time.sleep(0.02)
        ct["stage5_waitframe_ms"] = (time.perf_counter() - t0_wait) * 1000  # 等最终帧
        if final_col.processed >= 1:
            final_img = final_col.image(0)
        final_col.stop()
        ct["stage5_final_ms"] = (time.perf_counter() - t0) * 1000

        ct["main_total_ms"] = (time.perf_counter() - t_main) * 1000
        print(f"Δz = {deltaZ:.1f} μm, 预测位置 P = {P:.1f} μm")
        print_ct(ct)

        return {"rc": 0,
                "predicted_um":P,
                "roi":roi,
                "fine_positions":positions,
                "fine_scores":scores,
                "best_f":best_f,
                "final_image":final_img,
                "ct": ct}
    except Exception as e:
        return {"rc": 1, "error": str(e)}
    finally:
        if plc is not None:
            plc.disconnect()
        if cam is not None:
            cam.close()

def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # 中文日志不乱码
    args = build_parser().parse_args(argv)
    result = run_dl_hybrid(args)
    print(result["rc"],result["best_f"],result["fine_positions"])
    return 0 if result.get("rc") == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
