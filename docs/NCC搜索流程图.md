# NCC 模板匹配搜索流程图

```mermaid
flowchart LR
    START["NCCSearch 启动"] --> INIT["1. 初始化"]
    INIT --> C_LOOP{"粗扫完成?<br/>共21个点"}
    C_LOOP -->|"否: 输出下一个"| C_EVAL["外部拍图评价"]
    C_EVAL -->|"score 返回"| C_STORE["存入 _scores"]
    C_STORE --> C_LOOP
    C_LOOP -->|"是: 全部拍完"| NCC

    NCC["2. NCC 滑动匹配"] --> NCC_DETAIL["试 Δz=-99..99<br/>算 Pearson 相关系数<br/>取 argmax"]
    NCC_DETAIL --> Q["3. 判断 quality"]

    Q --> V_GEN["4. 生成验证列表<br/>预测峰 ±1<br/>排除已粗扫过的"]
    V_GEN --> V_LOOP{"验证完成?<br/>共2~3个点"}
    V_LOOP -->|"否: 输出下一个"| V_EVAL["外部拍图评价"]
    V_EVAL -->|"score 返回"| V_STORE["存入 _scores"]
    V_STORE --> V_LOOP
    V_LOOP -->|"是: 全部拍完"| FIT

    FIT["5. 二次拟合"] --> FIT_DETAIL["取已知点 top 3<br/>y=ax²+bx+c<br/>顶点=-b/2a"]
    FIT_DETAIL --> DONE["6. 完成<br/>输出 best_index"]

    style NCC fill:#e1f5fe,stroke:#0288d1
    style Q fill:#fff3e0,stroke:#f57c00
    style FIT fill:#e8f5e9,stroke:#388e3c
    style DONE fill:#c8e6c9,stroke:#2e7d32
```

## 验证环节详解

以 0729 数据为例，粗扫 21 个点、NCC 预测峰=68：

| 步骤 | 操作 | 说明 |
|---|---|---|
| 1 | 生成候选列表 [67, 68, 69] | 预测峰 ±1 |
| 2 | 排除已评价的 68 | 粗扫时 step=5，index=68 是 65→70 之间但 68 不在粗扫列表中... |
| | 实际 0729 粗扫位置: 0,5,10,...,95,99 | 68 不在其中 → 不需要排除 |
| 3 | 验证列表 = [67, 68, 69] | 3 个点都需拍图 |
| 4 | 拍 67 → score=4582 → 存入 | |
| 5 | 拍 68 → score=5659 → 存入 | 最高分 |
| 6 | 拍 69 → score=5495 → 存入 | |
| 7 | 验证完成 → 进入二次拟合 | |

> 如果粗扫步长=10 且粗扫点包含 70，则验证列表 [67,68,69] 中 68 和 70 以外的都需要拍。可能只有 2 个点。
