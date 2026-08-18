"""
视觉平台脚本：交互式粗扫+精扫对焦搜索 (Coarse-to-Fine)

功能:
    每次被平台循环调用时，读取全局变量 g_Score 和 g_SearchState，
    推进搜索状态机一步，输出 g_NextIndex 和 g_IsDone。
    首次调用时自动初始化搜索状态。

使用方式:
    循环前
        g_TotalImages = 100        # 拍摄总张数
        g_CoarseStep  = 0          # 0 = 自动 (N/10)
        g_SearchState = ""         # 清空触发自动初始化

    WHILE True:
        platform_search_step()     # 推进一步搜索
        IF g_IsDone == 1: BREAK

        MoveToWD(g_NextIndex)      # 运动轴移动到对应 WD 位置
        img = AcquireImage()       # 采图
        g_Score = Evaluate(img, ROI)  # ROI 清晰度评价

    // 搜索结束，读取结果
    best_idx = g_BestIndex
    best_val = g_BestScore
"""
import math
import json
class CoarseToFineSearch:
    """粗扫+精扫对焦搜索器"""

    def __init__(self, total_images: int, coarse_step: int = 0):
        # TODO 1: 计算步长、生成粗扫列表、设初始状态
        if total_images <= 0:
            raise ValueError("total_images must be > 0")

        # 确定粗扫步长
        if coarse_step <= 0:
            coarse_step = max(1, total_images // 10)

        # 生成粗扫序号列表
        coarse_indices = list(range(0, total_images, coarse_step))
        if coarse_indices[-1] != total_images - 1:
            coarse_indices.append(total_images - 1)

        # 设置初始状态
        self._phase = "coarse"
        self._scores = {} #一个字典，记录"哪张图得了多少分"。
        self._last_idx = coarse_indices[0] #第一个粗扫点
        self._step = coarse_step  # ← _coarse_to_fine 里要用来算精扫范围
        self._total = total_images  # ← _coarse_to_fine 和 stats 都要用
        self._coarse_idx = coarse_indices  # ← _step_coarse 里要遍历
        self._coarse_pos = 0  # 粗扫进度指针
        self._fine_idx = []  # ← _step_fine 里要遍历（粗扫完成后才填）
        self._fine_pos = 0  # ← _step_fine 里要推进

        pass

    # ---- 只读属性 ----
    @property
    def first_index(self) -> int:
        # TODO 2: 返回第一个要评价的序号
        return self._coarse_idx[0]

    @property
    def stats(self) -> dict:
        # 当前最优
        if self._scores:
            best_idx = max(self._scores.keys(), key=self._scores.get)
            best_score = self._scores[best_idx]
        else:
            best_idx = -1
            best_score = 0.0

        eval_count = len(self._scores)

        return {
            "phase": self._phase,
            "eval_count": eval_count,
            "total_images": self._total,
            "reduction_pct": (1 - eval_count / self._total) * 100 if self._total > 0 else 0,
            "coarse_step": self._step,
            "best_so_far": {"index": best_idx, "score": best_score},
            "scores": dict(sorted(self._scores.items())),
        }

    # ---- 核心方法 ----
    def next(self, score: float) -> tuple:
        # TODO 4: 喂入分数，返回 (next_index, is_done, best_index, best_score)
        # 1. 先把分数存起来
        self._scores[self._last_idx] = score

        # 2. 根据当前阶段推进
        if self._phase == "coarse":
            return self._step_coarse()
        elif self._phase == "fine":
            return self._step_fine()
        else:
            raise RuntimeError(f"未知阶段: {self._phase}")

    # ---- 内部方法 ----
    def _step_coarse(self):
        self._coarse_pos += 1
        if self._coarse_pos < len(self._coarse_idx):
            # 还有粗扫点 → 输出下一个
            next_idx = self._coarse_idx[self._coarse_pos]
            self._last_idx = next_idx
            return (next_idx, False, None, None)
        else:
            # 粗扫完毕 → 转精扫
            return self._coarse_to_fine()

    def _step_fine(self):
        self._fine_pos += 1

        if self._fine_pos < len(self._fine_idx):
            # 还有精扫点
            next_idx = self._fine_idx[self._fine_pos]
            self._last_idx = next_idx
            return (next_idx, False, None, None)
        else:
            # 精扫完毕，全局取最优
            best_idx = max(self._scores.keys(), key=self._scores.get)
            return self._finish(best_idx)

    def _coarse_to_fine(self):
        # 1. 找粗扫中的峰值
        peak_idx = max(self._scores.keys(), key=self._scores.get)

        # 2. 确定精扫范围 [peak - step, peak + step]
        lo = max(0, peak_idx - self._step)
        hi = min(self._total - 1, peak_idx + self._step)

        # 3. 排除粗扫已评过的点
        fine_indices = [i for i in range(lo, hi + 1) if i not in self._scores]

        if fine_indices:
            # 有需要精扫的点
            self._phase = "fine"
            self._fine_idx = fine_indices
            self._fine_pos = 0
            self._last_idx = fine_indices[0]
            return (fine_indices[0], False, None, None)
        else:
            # 粗扫已覆盖全部，直接结束
            return self._finish(peak_idx)

    def _finish(self, best_idx: int):
        best_score = self._scores[best_idx]
        self._last_idx = best_idx
        return (-1, True, best_idx, best_score)