import math
from focus_template import FocusTemplate


def ncc_predict_peak(
    coarse_positions: list[float],   # 粗扫帧的 µm 位置（用 frame_positions 生成）
    coarse_scores: list[float],      # 对应的 T 分数
    template,                        # FocusTemplate
    search_start_um: int,
    search_end_um: int,
    min_score: float = 0.5,
) -> tuple[float, float, str]:
    """返回 (predicted_peak_um, ncc_max, quality)。
    quality: ok / partial(0.5~0.9) / mismatch(<0.5) / boundary(预测峰贴边)"""

    def _determine_quality(
            ncc_max: float,
            predicted_peak_um: float,
            coarse_positions: list[float],
            coarse_scores: list[float],
            search_start_um: int,
            search_end_um: int,
            min_score: float = 0.5,
    ) -> str:
        # 粗扫步距从位置数组推导（第 2 帧 - 第 1 帧）
        coarse_step = coarse_positions[1] - coarse_positions[0] if len(coarse_positions) >= 2 else 100
        # 1. 边界：峰离任一端不足 1 个粗扫步距
        if (predicted_peak_um - search_start_um) < coarse_step or \
                (search_end_um - predicted_peak_um) < coarse_step:
            return "boundary"

        # 2. 低对比：分数太扁，没有峰
        cs_min, cs_max = min(coarse_scores), max(coarse_scores)
        if cs_min > 0 and cs_max / cs_min < 3:
            return "low_contrast"

        # 3. 匹配质量
        if ncc_max < min_score:
            return "mismatch"
        elif ncc_max < 0.9:
            return "partial"
        return "ok"
    # ── 第 1 步：从模板读出网格信息和峰位置 ──
    cal_start = template.meta["start_um"]  # 标定起点（9500）
    cal_step = template.meta["step_um"]  # 标定步距（5）
    N = len(template.curve)  # 模板点数（400）
    t_peak_um = cal_start + (template.peak_position + 1) * cal_step  # 模板峰 µm
    # ── 第 2 步：确定候选偏移 Δ 的范围（保证预测峰落在行程内）──
    dz_min = math.ceil((search_start_um - t_peak_um) / cal_step)
    dz_max = math.floor((search_end_um - t_peak_um) / cal_step)
    # ── 第 3 步：对每个 Δ 算 Pearson 相关（核心循环）──
    best_ncc, best_dz = -2.0, 0
    for dz_idx in range(dz_min, dz_max + 1):
        delta_um = dz_idx * cal_step
        pairs = []
        for p, s in zip(coarse_positions, coarse_scores):
            tidx = round((p - delta_um - cal_start) / cal_step) - 1  # 位置→模板下标
            if 0 <= tidx < N:
                pairs.append((s, template.curve[tidx]))
        if len(pairs) < 3:
            continue
        x = [pair[0] for pair in pairs]
        y = [pair[1] for pair in pairs]
        mx = sum(x) / len(x)
        my = sum(y) / len(y)
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
        ncc_val = cov / (sx * sy) if sx > 1e-10 and sy > 1e-10 else 0.0

        if ncc_val > best_ncc:
            best_ncc = ncc_val
            best_dz = dz_idx
    if best_ncc < -1.0:  # 一个有效 Δ 都没扫到
        return t_peak_um, 0.0, "mismatch"
    # ── 第 4 步：换算预测峰 ──
    predicted_peak_um = t_peak_um + best_dz * cal_step

    # ── 第 5 步：质量判断 ──

    quality =_determine_quality( best_ncc,predicted_peak_um,coarse_positions,coarse_scores,search_start_um,search_end_um,min_score=min_score)
    return predicted_peak_um, best_ncc, quality