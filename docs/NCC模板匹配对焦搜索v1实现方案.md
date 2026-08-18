# NCC 模板匹配对焦搜索 v1 实现方案

## 摘要

新增 `NCCSearch` 类，利用离线标定的完整模板曲线，通过归一化互相关（NCC）直接估计来料高度变化导致的峰值平移量 Δz。核心思路：场景满足 S(z) = F(z - Δz)，其中 F 是已知模板、Δz 是唯一未知量，NCC 在粗扫稀疏采样点上滑动模板找到最佳匹配偏移，然后验证+拟合输出亚 index 精度峰值。与 `CoarseToFineSearch` 平级独立，弱信号/失配时不自动退化，通过 `stats["quality"]` 暴露匹配质量。

## 新增文件

| 文件 | 职责 |
|---|---|
| `focus_template.py` | `FocusTemplate` 类：存储完整归一化曲线、FWHM、峰位置，支持 JSON 序列化 |
| `calibrate.py` | 离线标定脚本：全扫 → 生成模板 JSON → 退出 |

## 修改文件

| 文件 | 改动 |
|---|---|
| `platform_search.py` | 新增 `NCCSearch` 类 |
| `verify_offline.py` | 增加 NCC 策略对比测试（可选加载模板模拟生产场景） |

## Workflow

```
离线标定（只跑一次）
  calibrate.py --images ./0729/ --roi 2066,2662,300,300 --out template.json
    → 全扫 100 点 → 归一化曲线 + FWHM + 峰位置 → 保存 JSON

在线生产（每个新工件）
  template = FocusTemplate.load("template.json")
  search = NCCSearch(N=100, template=template)

  while not done:
      idx = search.next(score)
      采图 → 评价 → 得到 score

  结果 = search.stats
  检查 stats["quality"]:
      "ok"        → 直接用 best_index
      "partial"   → 曲线形状有变化但可接受
      "mismatch"  → 模板可能失效，换 CoarseToFineSearch 或重新标定
      "boundary"  → 峰在搜索边界，建议扩展范围
```

## 三阶段搜索流程

### Phase 1: 粗扫（11 点）

均匀采样，步长 = N // 10，共 11 个点（含首尾）。

```
位置:  0   10   20   30   40   50   60   70   80   90   99
分数: C0  C10  C20  C30  C40  C50  C60  C70  C80  C90  C99
```

### Phase 2: NCC 滑动匹配

对每个候选偏移 Δz ∈ [-(N-1), N-1]：

1. 对每个粗扫位置 p，取模板值 T(p - Δz)（仅当 0 ≤ p - Δz < N 时有效）
2. 收集所有有效配对 (C[p], T(p - Δz))
3. 计算 Pearson 相关系数（NCC）

NCC 公式：

```
         Σ (C_i - C̄)(T_i - T̄)
NCC = ─────────────────────────
       √Σ(C_i - C̄)² · √Σ(T_i - T̄)²
```

4. Δz* = argmax NCC
5. 预测峰位置 = template.peak_position + Δz*

计算量：11 个粗扫点 × 199 个候选偏移 = ~2200 次浮点运算，<1ms。

### Phase 3: 验证 + 二次拟合

在预测峰 ±1 处补采 2~3 个点（跳过已被粗扫覆盖的位置），取已知点中分数最高的 3 个做二次拟合 y = ax² + bx + c，顶点 x = -b/(2a) 即为亚 index 精度峰值。

```
总评价: 11 (粗扫) + 3 (验证) = 14 次
```

## 模拟验证结果（0729 数据）

| 真实偏移 Δz | NCC 估计 | 相关系数 | 峰值误差 | 总评价 |
|---|---:|---:|---:|
| -15 | -15 | 1.0000 | 0 | 14 |
| -10 | -10 | 1.0000 | 0 | 14 |
| -5 | -5 | 1.0000 | 0 | 14 |
| -1 | -1 | 1.0000 | 0 | 14 |
| 0 | 0 | 1.0000 | 0 | 14 |
| +3 | +3 | 1.0000 | 0 | 13 |
| +7 | +7 | 1.0000 | 0 | 14 |
| +15 | +15 | 1.0000 | 0 | 14 |
| +25 | +25 | 1.0000 | 0 | 14 |

NCC 在正确偏移处始终为 1.0，偏离 1 个 index 即骤降至 ~0.2。模板曲线的"平坦基线 + 尖峰"形态使相关性极尖锐。

## 实现要点

### 1. `FocusTemplate`（`focus_template.py`）

```python
{
    "curve": [0.000, 0.000, ..., 1.000, ..., 0.001],  # 全曲线 min-max 归一化
    "peak_position": 68,       # 模板峰所在 index
    "peak_width": 5.3,         # FWHM
    "shape_descriptor": [...], # ±2×FWHM 归一化片段（为 v2 NCC 精细匹配预留）
    "meta": {
        "total_images": 100,
        "roi": [2066, 2662, 300, 300],
        "score_min": 191.38,
        "score_max": 5659.30
    }
}
```

### 2. `NCCSearch`（`platform_search.py`）

构造函数：
```
NCCSearch(total_images, template, coarse_step=None)
```
- `template`：必需（与自适应方案不同，NCC 必须有模板）
- `coarse_step`：默认 N // 10

状态机：

| 阶段 | 触发 | 行为 |
|---|---|---|
| `coarse` | 初始 | 遍历粗扫点列表 |
| `ncc_match` | 粗扫结束 | 滑动模板计算 NCC，取 argmax 得 Δz* |
| `verify` | NCC 完成 | 在预测峰 ±1 补采 |
| `fit` | 验证完成 | 取最优 3 点做二次拟合，输出结果 |

### 3. 信号质量暴露（`stats["quality"]`）

| quality | 判据 | 调用方可选动作 |
|---|---|---|
| `"ok"` | NCC_max ≥ 0.9 且 R² ≥ 0.85 | 直接使用结果 |
| `"partial"` | 0.5 ≤ NCC_max < 0.9 | 曲线有变化但大致匹配，可接受 |
| `"mismatch"` | NCC_max < 0.5 | 模板可能失效，换粗精重跑或重新标定 |
| `"boundary"` | 预测峰在 [0, 3) 或 (N-4, N-1] | 峰接近搜索边界，建议扩展范围 |
| `"low_contrast"` | 粗扫 max/min < 3 | 峰谷区分度不足 |

### 4. 边界处理

- NCC 候选偏移范围：Δz ∈ [-(N-1), N-1]，保证覆盖峰从最左到最右的所有可能
- 有效配对过滤：仅当 0 ≤ p - Δz < N 时参与 NCC 计算
- 最少有效配对数：3（少于 3 个则该候选偏移 NCC 无效）
- 验证阶段：若预测峰已接近边界（<3 或 >N-4），quality 标记 `"boundary"`，只验证有效范围内的点

### 5. `calibrate.py`

```
python calibrate.py --images <path> --roi x,y,w,h [--out template.json]
```

- 全扫评价（复用 `OpenCVSharpnessEvaluator`）
- min-max 归一化全曲线存入 `curve` 字段
- 计算 FWHM（线性插值半高交点）
- 提取 shape_descriptor（±2×FWHM）
- 保存 JSON

## 与自适应梯度方案的对比

| | NCC 模板匹配 | 自适应梯度+二分 |
|---|---|---|
| 模板必需 | ✅ 是 | ❌ 否（可选） |
| 评价次数 | 14 | 14~16 |
| 核心算法 | 互相关 + argmax | 梯度检测 + 二分收敛 |
| 模板利用 | 完整曲线 | 仅 FWHM |
| 代码复杂度 | 低（一个 NCC 函数） | 中（4 阶段状态机） |
| 数学模型 | 直接求解 S(z)=F(z-Δz) | 通用极值搜索 |
| 弱信号 | NCC 相关系数自然偏低 | 梯度弱但仍能二分 |
| 可解释性 | NCC 曲线可可视化检查 | 状态机需逐步跟踪 |

## 测试方案

| 场景 | 输入 | 预期 |
|---|---|---|
| 正常偏移 | 模板 + 偏移 [-15, +25] | quality="ok", 误差 0, 评价 13~14 |
| 大偏移（边界） | 偏移 +45 | quality="boundary", 预测峰在 N 附近 |
| 模板失配 | 用其他产品的模板 | quality="mismatch", NCC < 0.5 |
| 低对比度 | 原始数据 ×0.1 + 噪声 | quality="low_contrast" |
| 生产模拟 | calibrate → 加载模板 → NCC 搜索 | 端到端流程正确 |

## 假设

- 离线标定必须完成（NCC 方案依赖模板），无法在无模板时运行
- 模板曲线形状在生产中保持稳定，仅峰值位置平移
- 粗扫步长固定为 N/10（无需 FWHM 调整，NCC 自身处理稀疏采样）
- 三种策略（CoarseToFine、AdaptiveGradient、NCC）平级独立，调用方选择
