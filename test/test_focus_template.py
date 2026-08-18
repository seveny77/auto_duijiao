import json
from focus_template import FocusTemplate

with open("../_scores.json", encoding="utf-8-sig") as f:  # 前提：之前转过 JSON
    scores = json.load(f)

t = FocusTemplate.from_fullscan(scores, roi=(2066, 2662, 300, 300))
print(f"peak_position: {t.peak_position}")    # 预期 68
print(f"peak_width:    {t.peak_width:.2f}")   # 预期 5.29
print(f"shape len:     {len(t.shape_descriptor)}")
print(f"z_offset:      {t.z_offset[0]} ~ {t.z_offset[-1]}")

# roundtrip 测试
t.save("_test_template.json")
t2 = FocusTemplate.load("../data/_test_template.json")
assert t2.peak_position == t.peak_position
assert t2.peak_width == t.peak_width
print("roundtrip ✓")