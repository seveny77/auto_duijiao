# test_ncc_search.py
import json
from focus_template import FocusTemplate
from search import CoarseToFineSearch, NCCSearch           # 推荐

# 加载真实数据模拟"在线工件"
with open("../_scores.json", encoding="utf-8-sig") as f:
    all_scores = json.load(f)

template = FocusTemplate.load("../data/template.json")
n = 100

def evaluate(idx):
    """模拟在线评价：直接读取全扫数据"""
    return all_scores[idx]

# NCC 搜索
search = NCCSearch(n, template)

idx = search.first_index
score = evaluate(idx)
print(f"Phase={search.stats['phase']}, idx={idx}, score={score:.1f}")

while True:
    idx, done, best_i, best_s = search.next(score)
    if done:
        break
    score = evaluate(idx)
    print(f"Phase={search.stats['phase']}, idx={idx}, score={score:.1f}")

s = search.stats
print(f"\n=== 结果 ===")
print(f"评估次数: {s['eval_count']}/{n} (-{s['reduction_pct']:.0f}%)")
print(f"NCC: {s['ncc_max']:.4f}, shift={s['ncc_shift']}")
print(f"预测峰: {s['predicted_peak']}")
print(f"最终峰: {best_i}, 真实峰: 68")
print(f"quality: {s['quality']}")
print(f"scores: {s['scores']}")
