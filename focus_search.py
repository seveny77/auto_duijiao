import cv2
import numpy as np
from dataclasses import dataclass
from typing import Tuple, Optional
import time

# ============================================================
# 1. Sharpness metrics
# ============================================================

def sharpness_laplacian(image, roi=None):
    if roi:
        x, y, w, h = roi
        patch = image[y:y+h, x:x+w]
    else:
        patch = image
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(patch, cv2.CV_64F).var()

def sharpness_tenengrad(image, roi=None):
    if roi:
        x, y, w, h = roi
        patch = image[y:y+h, x:x+w]
    else:
        patch = image
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(patch, cv2.CV_64F, 1, 0)
    gy = cv2.Sobel(patch, cv2.CV_64F, 0, 1)
    return np.mean(gx**2 + gy**2)

def sharpness_brenner(image, roi=None):
    if roi:
        x, y, w, h = roi
        patch = image[y:y+h, x:x+w]
    else:
        patch = image
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float64)
    else:
        patch = patch.astype(np.float64)
    diff = patch[:, 2:] - patch[:, :-2]
    return np.mean(diff ** 2)

def sharpness_variance(image, roi=None):
    if roi:
        x, y, w, h = roi
        patch = image[y:y+h, x:x+w]
    else:
        patch = image
    if patch.ndim == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float64)
    else:
        patch = patch.astype(np.float64)
    return float(np.var(patch))

METRICS = {
    "laplacian": sharpness_laplacian,
    "tenengrad": sharpness_tenengrad,
    "brenner":  sharpness_brenner,
    "variance": sharpness_variance,
}


# ============================================================
# 2. Evaluator: image_path + ROI -> score
# ============================================================

@dataclass
class SharpnessEvaluator:
    metric: str = "laplacian"
    def evaluate(self, image_path: str, roi: Optional[Tuple[int,int,int,int]] = None) -> float:
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Cannot read: {image_path}")
        return METRICS[self.metric](img, roi)


# ============================================================
# 3. Search engine
# ============================================================

class FocusSearch:
    def __init__(self, image_paths, evaluator, roi=None):
        self.paths = image_paths
        self.evaluator = evaluator
        self.roi = roi
        self.cache = {}
        self.eval_count = 0

    def _eval(self, idx):
        if idx not in self.cache:
            self.cache[idx] = self.evaluator.evaluate(self.paths[idx], self.roi)
            self.eval_count += 1
        return self.cache[idx]

    # ---- Full scan (baseline) ----
    def full_scan(self):
        for i in range(len(self.paths)):
            self._eval(i)
        best = max(self.cache, key=self.cache.get)
        return best, self.cache[best]

    # ---- Golden Section (recommended) ----
    def golden_section(self):
        n = len(self.paths)
        if n <= 2:
            for i in range(n):
                self._eval(i)
            best = max(self.cache, key=self.cache.get)
            return best, self.cache[best]

        phi = (np.sqrt(5) - 1) / 2
        lo, hi = 0, n - 1
        x1 = hi - int(phi * (hi - lo))
        x2 = lo + int(phi * (hi - lo))
        if x1 == x2:
            x2 = min(x1 + 1, hi); x1 = max(x1 - 1, lo)
        f1, f2 = self._eval(x1), self._eval(x2)

        while hi - lo > 2:
            if f1 > f2:
                hi = x2; x2, f2 = x1, f1
                x1 = hi - int(phi * (hi - lo))
                if x1 == x2: x1 = max(lo, x2 - 1)
                f1 = self._eval(x1)
            else:
                lo = x1; x1, f1 = x2, f2
                x2 = lo + int(phi * (hi - lo))
                if x1 == x2: x2 = min(hi, x1 + 1)
                f2 = self._eval(x2)

        for i in range(lo, hi + 1):
            self._eval(i)
        best = max(self.cache, key=self.cache.get)
        return best, self.cache[best]

    # ---- Coarse-to-Fine (robust) ----
    def coarse_to_fine(self, coarse_step=None):
        n = len(self.paths)
        if coarse_step is None:
            coarse_step = max(1, n // 10)

        coarse_idx = list(range(0, n, coarse_step))
        if coarse_idx[-1] != n - 1:
            coarse_idx.append(n - 1)

        scores = [(i, self._eval(i)) for i in coarse_idx]
        peak = max(scores, key=lambda x: x[1])[0]

        lo, hi = max(0, peak - coarse_step), min(n-1, peak + coarse_step)
        done = set(dict(scores).keys())
        for i in range(lo, hi + 1):
            if i not in done:
                self._eval(i)

        best = max(self.cache, key=self.cache.get)
        return best, self.cache[best]

    # ---- Quadratic Interpolation ----
    def quadratic(self, num_samples=5):
        n = len(self.paths)
        idxs = np.unique(np.linspace(0, n-1, num_samples, dtype=int))
        vals = [self._eval(int(i)) for i in idxs]
        a, b, _ = np.polyfit(idxs.astype(float), vals, 2)
        if a < 0:
            pk = max(0, min(n-1, int(round(-b / (2*a)))))
            for di in range(-1, 2):
                if 0 <= pk + di < n:
                    self._eval(pk + di)
        best = max(self.cache, key=self.cache.get)
        return best, self.cache[best]

    # ---- Hybrid: golden + local fine scan ----
    def hybrid(self, fine_radius=3):
        idx, _ = self.golden_section()
        lo = max(0, idx - fine_radius)
        hi = min(len(self.paths)-1, idx + fine_radius)
        for i in range(lo, hi + 1):
            self._eval(i)
        best = max(self.cache, key=self.cache.get)
        return best, self.cache[best]


# ============================================================
# 4. One-call API
# ============================================================

def find_sharpest(image_paths, roi=None, method="hybrid",
                  metric="laplacian", fine_radius=3) -> dict:
    """
    Find sharpest image in WD-ordered sequence.

    Args:
        image_paths: paths sorted by increasing working distance
        roi: (x, y, w, h) or None
        method: "hybrid"(recommended) | "golden" | "coarse" | "quad" | "full"
        metric: "laplacian" | "tenengrad" | "brenner" | "variance"
        fine_radius: local fine-scan radius for hybrid mode

    Returns:
        dict: best_path, best_index, score, eval_count, total_images,
              reduction_pct, elapsed_sec, cache
    """
    evaluator = SharpnessEvaluator(metric=metric)
    searcher = FocusSearch(image_paths, evaluator, roi)

    t0 = time.perf_counter()
    dispatch = {
        "full":    searcher.full_scan,
        "golden":  searcher.golden_section,
        "coarse":  searcher.coarse_to_fine,
        "quad":    searcher.quadratic,
        "hybrid":  lambda: searcher.hybrid(fine_radius),
    }
    best_idx, score = dispatch[method]()
    elapsed = time.perf_counter() - t0

    return {
        "best_path":      image_paths[best_idx],
        "best_index":     best_idx,
        "score":          score,
        "eval_count":     searcher.eval_count,
        "total_images":   len(image_paths),
        "reduction_pct":  (1 - searcher.eval_count / len(image_paths)) * 100,
        "elapsed_sec":    elapsed,
        "cache":          dict(sorted(searcher.cache.items())),
    }


# ============================================================
# Demo
# ============================================================
if __name__ == "__main__":
    print("=" * 75)
    print("  Focus Search Strategy Benchmark")
    print("=" * 75)
    print()

    for n, noise in [(100, 0.01), (200, 0.02), (500, 0.02)]:
        true_peak = int(n * 0.65)
        sigma = n * 0.15
        x = np.arange(n)
        scores = np.exp(-0.5 * ((x - true_peak) / sigma) ** 2)
        scores += np.random.normal(0, noise, n)

        class MockEval:
            def evaluate(self, p, roi=None):
                return scores[int(p.split("_")[1].split(".")[0])]

        paths = [f"img_{i:04d}.png" for i in range(n)]

        print(f"[n={n:3d}, peak~{true_peak}, noise={noise}]")
        print(f"  {'Method':<20s} {'Evals':>6s}  {'Reduction':>8s}  {'Idx':>5s}  {'Err':>5s}")
        print(f"  {'-'*20} {'-'*6}  {'-'*8}  {'-'*5}  {'-'*5}")

        for method in ["full", "coarse", "golden", "hybrid", "quad"]:
            s = FocusSearch(paths, MockEval(), None)
            m = {"full": s.full_scan, "coarse": s.coarse_to_fine,
                 "golden": s.golden_section, "quad": s.quadratic,
                 "hybrid": lambda: s.hybrid(3)}
            idx, sc = m[method]()
            red = (1 - s.eval_count / n) * 100
            err = abs(idx - true_peak)
            print(f"  {method:<20s} {s.eval_count:3d}/{n:<4d} {red:6.1f}%  {idx:5d}  {err:5.1f}")
        print()
