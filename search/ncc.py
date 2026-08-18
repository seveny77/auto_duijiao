import math

class NCCSearch:
    """NCC 模板匹配对焦搜索器。

    需要 FocusTemplate（离线标定生成）。
    与 CoarseToFineSearch 平级独立，共享相同接口。
    """

    def __init__(self, total_images, template, coarse_step=None):
        if coarse_step is None:
            if template.peak_width and template.peak_width > 0:
                coarse_step = max(2, int(template.peak_width))
            else:
                coarse_step = max(1, total_images // 10)

        self._total = total_images
        self._template = template
        self._step = coarse_step

        coarse_indices = list(range(0, total_images, coarse_step))
        if coarse_indices[-1] != total_images - 1:
            coarse_indices.append(total_images - 1)

        self._coarse_idx = coarse_indices
        self._coarse_pos = 0
        self._phase = "coarse"
        self._scores = {}
        self._last_idx = coarse_indices[0]

        self._verify_idx = []
        self._verify_pos = 0

        self._fit_verify_idx = []
        self._fit_verify_pos = 0

        self._ncc_max = None
        self._ncc_shift = None
        self._predicted_peak = None
        self._quality = None

    @property
    def first_index(self):
        return self._coarse_idx[0]

    @property
    def stats(self):
        if self._scores:
            best_idx = max(self._scores, key=self._scores.get)
            best_score = self._scores[best_idx]
        else:
            best_idx = -1
            best_score = 0.0

        result = {
            "phase": self._phase,
            "eval_count": len(self._scores),
            "total_images": self._total,
            "reduction_pct": (1 - len(self._scores) / self._total) * 100
            if self._total > 0 else 0,
            "best_so_far": {"index": best_idx, "score": best_score},
            "scores": dict(sorted(self._scores.items())),
        }
        if self._ncc_max is not None:
            result["ncc_max"] = self._ncc_max
            result["ncc_shift"] = self._ncc_shift
            result["predicted_peak"] = self._predicted_peak
            result["quality"] = self._quality
        return result

    def next(self, score):
        self._scores[self._last_idx] = score

        if self._phase == "coarse":
            return self._step_coarse()
        elif self._phase == "verify":
            return self._step_verify()
        elif self._phase == "fit_verify":
            return self._step_fit_verify()
        else:
            raise RuntimeError(f"未知阶段: {self._phase}")

    def _step_coarse(self):
        self._coarse_pos += 1
        if self._coarse_pos < len(self._coarse_idx):
            next_idx = self._coarse_idx[self._coarse_pos]
            self._last_idx = next_idx
            return (next_idx, False, None, None)
        else:
            return self._coarse_to_verify()

    def _step_verify(self):
        self._verify_pos += 1
        if self._verify_pos < len(self._verify_idx):
            next_idx = self._verify_idx[self._verify_pos]
            self._last_idx = next_idx
            return (next_idx, False, None, None)
        else:
            return self._verify_to_fit()

    def _step_fit_verify(self):
        self._fit_verify_pos += 1
        if self._fit_verify_pos < len(self._fit_verify_idx):
            next_idx = self._fit_verify_idx[self._fit_verify_pos]
            self._last_idx = next_idx
            return (next_idx, False, None, None)
        else:
            return self._finish()

    def _coarse_to_verify(self):
        coarse_scores = [self._scores[idx] for idx in self._coarse_idx]
        n = self._total
        t_curve = self._template.curve
        t_peak = self._template.peak_position

        best_dz = 0
        best_ncc = -2.0

        for dz in range(-t_peak, n - t_peak):
            valid_pairs = []
            for i, p in enumerate(self._coarse_idx):
                tidx = p - dz
                if 0 <= tidx < n:
                    valid_pairs.append((coarse_scores[i], t_curve[tidx]))

            if len(valid_pairs) < 3:
                continue

            x = [pair[0] for pair in valid_pairs]
            y = [pair[1] for pair in valid_pairs]
            mx = sum(x) / len(x)
            my = sum(y) / len(y)
            cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
            sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
            sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
            ncc_val = cov / (sx * sy) if sx > 1e-10 and sy > 1e-10 else 0.0

            if ncc_val > best_ncc:
                best_ncc = ncc_val
                best_dz = dz

        self._ncc_shift = best_dz
        self._ncc_max = best_ncc
        self._predicted_peak = t_peak + best_dz
        self._quality = self._determine_quality()

        verify_set = set()
        for dp in [-2, -1, 0, 1, 2]:
            vp = self._predicted_peak + dp
            if 0 <= vp < n:
                verify_set.add(vp)
        self._verify_idx = [v for v in sorted(verify_set) if v not in self._scores]

        if self._verify_idx:
            self._phase = "verify"
            self._verify_pos = 0
            self._last_idx = self._verify_idx[0]
            return (self._verify_idx[0], False, None, None)
        else:
            return self._verify_to_fit()

    def _verify_to_fit(self):
        """验证完成 → 二次拟合 → 如果拟合点没被评过，补评一次"""
        best_items = sorted(self._scores.items(), key=lambda x: x[1], reverse=True)[:3]
        xs = [item[0] for item in best_items]
        ys = [item[1] for item in best_items]

        if len(xs) < 3:
            self._fit_best_idx = xs[0]
        else:
            x0, x1, x2 = xs
            y0, y1, y2 = ys
            denom = x2 - x0
            if abs(denom) < 1e-10:
                self._fit_best_idx = max(self._scores, key=self._scores.get)
            else:
                a = ((y2 - y1) / (x2 - x1) - (y1 - y0) / (x1 - x0)) / denom
                b = (y1 - y0) / (x1 - x0) - a * (x0 + x1)
                peak_fit = -b / (2 * a)
                peak_fit = max(0.0, min(float(self._total - 1), peak_fit))
                self._fit_best_idx = int(round(peak_fit))

        # 拟合结果没被评过 → 补评一次
        if self._fit_best_idx not in self._scores:
            self._fit_verify_idx = [self._fit_best_idx]
            self._fit_verify_pos = 0
            self._phase = "fit_verify"
            self._last_idx = self._fit_best_idx
            return (self._fit_best_idx, False, None, None)
        else:
            return self._finish()

    def _determine_quality(self):
        n = self._total

        if self._predicted_peak < 3 or self._predicted_peak > n - 4:
            return "boundary"

        coarse_scores = [self._scores[idx] for idx in self._coarse_idx]
        cs_min, cs_max = min(coarse_scores), max(coarse_scores)
        if cs_min > 0 and cs_max / cs_min < 3:
            return "low_contrast"

        if self._ncc_max < 0.5:
            return "mismatch"
        elif self._ncc_max < 0.9:
            return "partial"

        return "ok"

    def _finish(self):
        best_known_idx = max(self._scores, key=self._scores.get)

        if (self._fit_best_idx in self._scores and
                self._scores[self._fit_best_idx] < self._scores[best_known_idx]):
            best_idx = best_known_idx
        else:
            best_idx = self._fit_best_idx

        best_score = self._scores.get(best_idx, 0.0)
        return (-1, True, best_idx, best_score)
