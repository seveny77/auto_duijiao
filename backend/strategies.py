# backend/strategies.py
"""搜索策略接口与注册表：NCC / DL 统一抽象。

接口输入是"采图能力"（SearchContext），而非某一方的具体数据。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Optional, Type


@dataclass
class PeakPrediction:
    """策略预测结果：峰位置 + 质量 + 供后续阶段使用的数据。"""

    peak_um: float
    quality: str
    ncc_max: float = 0.0
    roi_frame: Optional[object] = None    # 供 ROI 检测的帧（NCC=粗扫最佳帧，DL=单帧）
    coarse_points: list = field(default_factory=list)  # 曲线数据 [(位置µm, 分数)]
    extra: dict = field(default_factory=dict)


class SearchContext:
    """策略的采图环境：只暴露能力，不暴露细节。"""

    def __init__(self, cam, plc, evaluator, cfg, template):
        self._cam = cam
        self._plc = plc
        self._eval = evaluator
        self.cfg = cfg
        self.template = template

    def coarse_scan(self, start_um, end_um, step_um):
        """均匀粗扫飞拍，返回 (positions, scores, best_frame)。NCC 用。"""

        # 延迟导入：
        # strategies.py 会被 pipeline.py 导入，而 coarse_scan 又需要使用
        # pipeline.run_phase。放在方法内部可避免模块初始化阶段循环导入。
        from backend.camera_utils import frame_positions
        from backend.collector import save_phase_images
        from backend.pipeline import run_phase

        # PLC 飞拍约定为“含终点、不含起点”时，
        # 帧数等于扫描跨度除以步距。
        n = (end_um - start_um) // step_um

        # 执行粗扫。
        # save_all=True 时，PhaseCollector 在评价每帧后立即保存：
        # img_0000.jpg、img_0001.jpg……
        col, count, _ = run_phase(
            self._plc,
            self._cam,
            self._eval,
            start_um,
            end_um,
            step_um,
            n,
            save_dir=(
                self.cfg.save_dir
                if self.cfg.save_all
                else None
            ),
            start_index=0,
            flyscan_timeout=self.cfg.flyscan_timeout,
            wait_timeout=self.cfg.frame_wait_timeout,
            save_all=self.cfg.save_all,
            cancel_event=self.cfg.cancel_event,
            preview_callback=self.cfg.preview_callback,
            preview_interval_s=self.cfg.preview_interval_s,
            phase_name="coarse",
        )

        # 粗扫结束后立即停止取流。
        # 后面的 ROI 检测/YOLO 不应与相机回调并发运行。
        col.stop()

        # PhaseCollector.scores() 返回：
        # {帧序号: 清晰度得分}
        scores_map = col.scores()

        # 按帧序号恢复为有序列表。
        scores = [
            scores_map.get(i)
            for i in range(count)
        ]

        # run_phase 已经检查处理数量，但这里仍检查分数表中的序号是否完整。
        # 如果中间某个序号缺失，必须给出明确错误，不能继续做 NCC。
        if any(score is None for score in scores):
            raise RuntimeError("粗扫缺帧，请检查")

        # 将帧序号换算为实际 Z 位置。
        positions = frame_positions(
            count,
            start_um,
            step_um,
        )

        # save_images 与 save_all 是两种不同保存模式。
        #
        # save_images 保存带阶段、序号和实际位置的文件：
        # coarse_0000_14900um.jpg
        # coarse_0001_14950um.jpg
        if self.cfg.save_images:
            save_phase_images(
                col,
                count,
                positions,
                "coarse",
                self.cfg.save_images,
            )

        # 找到粗扫得分最高的帧，交给后续 ROI 检测。
        best = max(
            range(count),
            key=lambda i: scores[i],
        )
        best_frame = col.image(best)
        return positions, scores, best_frame

    def capture_frame(self):
        """拍一张定拍帧，供 DL 策略使用。"""

        # 具体的软件触发、取帧和停止取流流程，
        # 统一交给相机适配器管理。
        return self._cam.capture_frame(
            timeout_ms=1000
        )


class FocusStrategy(ABC):
    """搜索策略基类：策略自行决定采集方式，返回峰位置预测。"""

    name: str = ""

    @abstractmethod
    def predict_peak(self, ctx: SearchContext) -> PeakPrediction:
        raise NotImplementedError


STRATEGIES: Dict[str, Type[FocusStrategy]] = {}


def register(cls):
    """注册表装饰器。"""
    STRATEGIES[cls.name] = cls
    return cls


@register
class NCCStrategy(FocusStrategy):
    """NCC 策略：内部执行粗扫 + NCC 预测。"""

    name = "ncc"

    def predict_peak(self, ctx: SearchContext) -> PeakPrediction:
        from backend.ncc import ncc_predict_peak

        positions, scores, best_frame = ctx.coarse_scan(
            ctx.cfg.search_start_um,
            ctx.cfg.search_start_um + ctx.cfg.search_span_um,
            ctx.cfg.coarse_step_um,
        )
        peak_um, ncc_max, quality = ncc_predict_peak(
            positions, scores, ctx.template,
            ctx.cfg.search_start_um,
            ctx.cfg.search_start_um + ctx.cfg.search_span_um,
            min_score=ctx.cfg.ncc_min_score,
        )
        return PeakPrediction(
            peak_um=peak_um,
            quality=quality,
            ncc_max=ncc_max,
            roi_frame=best_frame,
            coarse_points=list(zip(positions, scores)),
        )


@register
class DLStrategy(FocusStrategy):
    """DL 策略：单帧推理 Δz（模型部分可先占位，验证接口）。"""

    name = "dl"

    def predict_peak(self, ctx: SearchContext) -> PeakPrediction:
        img = ctx.capture_frame()
        # TODO: 接入 DLDistanceModel：deltaZ = model.predict_frame(img)
        deltaZ = 0.0
        shot = ctx.cfg.shot_position_um or (ctx.cfg.search_start_um + ctx.cfg.search_span_um // 2)
        P = shot + deltaZ
        P = max(ctx.cfg.search_start_um,
                min(ctx.cfg.search_start_um + ctx.cfg.search_span_um, P))
        return PeakPrediction(peak_um=P, quality="dl_placeholder", roi_frame=img)