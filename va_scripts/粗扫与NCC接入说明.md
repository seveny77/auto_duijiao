# VA 粗扫数据记录与 NCC

本次新增两个独立 Python 文件，只使用标准库，不包含软件变量接口：

- `coarse_scan.py`：初始化粗扫 JSON、逐帧追加绝对位置与清晰度。
- `coarse_ncc.py`：读取完整的标定 JSON 和粗扫 JSON，计算一维 NCC 并返回结果 JSON。

不修改你已经在 VA 中运行的两个标定脚本，也不要求标定 JSON 增加字段。
当前假定粗扫也采用起点不触发、终点触发、按触发顺序逐帧执行一次，单位为 um。
这一版在粗扫全部结束后计算 NCC，不进行中途提前停止，不控制电机。

## 在三个节点中调用

你可以像标定脚本一样，把 `coarse_scan.py` 的全部代码分别复制到初始化和更新节点。
把 `coarse_ncc.py` 的全部代码复制到粗扫结束后的计算节点。
三个节点均自行添加变量读写代码；文件之间不需要相互 import。
也可把 NCC 放到粗扫循环的最后一帧，但必须等当帧数据追加成功、complete 为 true 后调用。

所有示例中的变量都是 Python 普通参数，不是 VA 中预设的软件变量名称。
不要把粗扫记录写入标定变量 `#biaoDataSet`，两套数据需要分开保存。

### 节点一：粗扫初始化，运动开始前执行一次

```python
# 先通过你自己的接口读取粗扫起点、终点、步距，单位均为 um。
# 这里的参数是粗扫参数，不要求等于标定参数。
coarse_result_json = initialize_coarse_scan(
    start_position=coarse_start,
    end_position=coarse_end,
    step=coarse_step,
)

# 将 coarse_result_json 存到单独的粗扫 String 变量。
```

### 节点二：粗扫循环更新，每帧清晰度计算结束后执行一次

```python
# current_sharpness：你已确认接口返回的单个清晰度数值。
# previous_coarse_json：从粗扫 String 变量读取的上一帧累计结果。
coarse_result_json = append_coarse_sharpness(
    sharpness=current_sharpness,
    start_position=coarse_start,
    end_position=coarse_end,
    step=coarse_step,
    history_json=previous_coarse_json,
)

# 将 coarse_result_json 写回同一个粗扫 String 变量。
```

粗扫数据沿用标定的 `samples` 格式，并额外记录 `"scan_type":"coarse"`。
函数防止直接把旧标定 JSON 当作粗扫历史续写，但无法阻止调用方把返回结果写到错误变量。
初始化只执行一次，每帧不要重置。采满后继续追加会报错。

### 节点三：NCC 计算，粗扫结束后执行一次

```python
# calibration_json：从 #biaoDataSet 读取的完整标定 JSON 字符串。
# coarse_json：本轮完整粗扫 JSON 字符串。
ncc_result_json = calculate_coarse_ncc(
    calibration_json=calibration_json,
    coarse_json=coarse_json,
)

# ncc_result_json 为 str，可存到单独的 NCC 结果 String 变量。
ncc_result = json.loads(ncc_result_json)

ncc_valid = ncc_result["valid"]
ncc_quality = ncc_result["quality"]
ncc_score = ncc_result["ncc_max"]

# 默认清空本轮可用的精扫中心，避免失败后误用上一轮成功位置。
fine_center_um = None
if ncc_valid:
    fine_center_um = ncc_result["predicted_peak_um"]
    # 由你的流程设置精扫中心、检查运动范围，再进入精扫。
else:
    # 由你的流程判失败或补拍，不自动使用预测值，不回退到旧位置。
    print(ncc_result["message"])
```

NCC 计算或解析若抛异常，也必须按本轮失败处理，不能让流程沿用上次成功结果。
`None` 在 JSON 中表现为 `null`，不要直接写入不接受空值的数值变量。
`valid` 是本轮是否可采用预测值的判断依据，即使 `predicted_peak_um` 有值也必须检查它。

### 临时联调：忽略质量拦截，先跑通流程

用户需要先联通整体流程时，使用 `ncc_debug_call.py` 的调用片段，替换节点中原来的
`calculate_coarse_ncc(...)` 调用、`if not result["valid"]: raise ...` 以及结果提取部分。
保留上方的 NCC 函数定义与自己的输入变量读取；不必修改标定、粗扫记录或 NCC 算法。

- `DEBUG_MODE=True`：低 NCC、边界峰、歧义等质量结果不阻止联调。有候选时使用候选，
  无候选时使用粗扫实测最高点继续流程，来源记录在 `flow_position_source`。
- `DEBUG_MODE=False`：恢复正式检查，`valid=false` 时抛出异常。
- 输入损坏、未采完、非数值等计算错误不忽略；输出位置仍须是本轮粗扫行程内的有限数。
- 原始 `valid`、`quality` 和 NCC 值保持不变；新增 `flow_continue` 表示本轮是否已获准
  继续联调，`flow_peak_um` 是实际选给后续流程的位置。`flow_continue` 不代表对焦成功。
- 后续节点若也按 `valid` 拦截，联调阶段需改为使用本轮新增的 `flow_continue`；
  不要读取上轮遗留标志。任何输入/计算异常仍应停止本轮并避免沿用上轮输出。
- 没有 NCC 候选时 `ncc_score` 和 `shift_um` 为 None；如需写入数值软件变量，需由调用方
  约定无值处理方式。不要把粗扫实测最高点当作 NCC 预测成功。
- 不关闭 PLC/电机限位；下游精扫起终点需处于设备允许行程内。调试开关不能保障精度。

用户本例在联调模式下将输出候选位置 10525 um，并保留 `valid=false`、
`quality=low_ncc`、NCC 约 0.6521；切回严格模式则仍然拒绝。这不需要把 NCC 门槛改低。

## 计算方法与位置含义

标定给出细步距的清晰度曲线 T(z)，粗扫给出位置 p_i 上的清晰度 C_i。
假设来料差异主要导致曲线沿 Z 轴平移，并允许清晰度有正比例缩放与常量偏置：

```text
C(z) ≈ a × T(z - Δz) + b，a > 0

NCC(Δz) = Σ[(C_i - mean(C)) × (T_i - mean(T))]
          / sqrt(Σ(C_i - mean(C))² × Σ(T_i - mean(T))²)

T_i = T(p_i - Δz)
预测焦点 = 标定曲线最大清晰度的位置 + 最佳 Δz
```

这是去均值归一化互相关（ZNCC，也即这里采用的 Pearson 相关），范围为 [-1, 1]。
每个候选使用同一组有效配对计算均值和分母，不需要先把两套数据分别 min-max 归一化。
NCC 分数不是概率。

- 标定和粗扫都使用绝对位置。两者可有不同起终点、步距和采样方向。
- 在候选位置落在标定采样点之间时线性插值；不要求粗扫步距是标定步距的整数倍。
- 候选位置超出标定采样覆盖范围时忽略该对数据，不补零、不外推，也不编造未采的起点。
- 默认按标定步距搜索 Δz，确保候选包含零偏移，且预测焦点位于粗扫行程内。
- 正 `shift_um` 表示本轮焦点比标定焦点更靠近绝对坐标的正方向，与扫描正反向无关。
- 单一最大值时，以标定最大值位置为基准；同一宽峰顶上的重复最大值，以最左与最右
  最大值位置的中点为参考，同时输出范围。本阶段不做亚采样峰值拟合。
- 多个最大值之间若有浅凹陷，默认凹陷深度不超过标定全曲线极差的 5%，仍作为同一
  宽峰顶；更深的谷仍以 `ambiguous_template` 拒绝。这是可配置的工程判据，不是
  对整数化、噪声或真实峰数量的硬件诊断。原始曲线不会被平滑、加扰动或改值。

## 结果字段

| 字段 | 含义 |
| --- | --- |
| `valid` | 只有通过本版质量检查才为 true，用来决定是否进入精扫 |
| `quality` / `message` | 状态码 / 中文说明 |
| `predicted_peak_um` | NCC 估计的焦点绝对位置，可能为 null |
| `shift_um` | 相对标定曲线的平移量，可能为 null |
| `ncc_max` | 最佳 NCC，可能为 null |
| `template_peak_um` | 单一最大值位置，或宽峰顶的代表中点；分离多峰时为 null |
| `template_peak_method` | `single_maximum` / `plateau_midpoint` / `unresolved_multiple_peaks` |
| `template_peak_range_um` | 最左到最右最大值的位置范围；单点时两端相同 |
| `template_peak_count` | 标定中等于最大清晰度的采样点数量 |
| `template_peak_valley_ratio` | 上述最大值之间凹陷深度除以标定全曲线极差，可能为 null |
| `predicted_peak_range_um` | 标定峰顶范围加最佳偏移；用于设计精扫覆盖范围，不是统计置信区间 |
| `coarse_best_position_um` | 粗扫实测清晰度最大的位置，不等于 NCC 预测位置 |
| `coarse_best_sharpness` | 粗扫实测最大清晰度 |
| `matched_points` / `coarse_count` | 最佳候选的有效配对数量 / 粗扫总点数 |
| `overlap_ratio` | 最佳候选有效配对数量除以粗扫总点数 |
| `required_points` | 每个候选至少需要的有效配对数量 |
| `match_step_um` | 本次偏移搜索的步距 |
| `second_peak_um` / `second_ncc` | 距最佳峰至少一个粗扫步距的另一候选及其分数，可能为 null |
| `ncc_gap` | 最佳分数减上述另一个候选分数，可能为 null |
| `valid_candidates` | 满足配对数和非平坦要求的候选数量，不表示质量检查通过的数量 |

例如标定峰为 100 um，本轮曲线平移 +14 um，粗扫只测到 110、120 等位置，
仍可能预测出 114 um；该位置还没有实拍，需要后续精扫验证。

### 质量状态

| quality | 说明 |
| --- | --- |
| `ok` | 匹配通过；用于精扫中心，不代表已经验证最终焦点 |
| `insufficient_points` | 粗扫不足默认 5 点，或标定不足 3 点 |
| `flat_curve` | 标定或粗扫清晰度几乎没有变化，NCC 不可靠 |
| `ambiguous_template` | 多个最大值之间有明显低谷，不能合并成同一宽峰顶 |
| `template_boundary` | 标定峰在标定采样边缘，没有完整包围峰 |
| `no_match` | 没有足够重叠且非平坦的候选 |
| `low_ncc` | 最佳 NCC 低于阈值 |
| `boundary` | 预测峰靠近行程边界、预测峰顶范围触及行程边界，或匹配数据没有包围峰顶范围 |
| `ambiguous` | 相隔至少一个粗扫步距的另一位置也具有接近的相关性 |

输入 JSON 损坏、未完整采集、单位错误、位置序号不一致等属于输入错误，直接抛出
`ValueError`，不会返回一个可用的预测位置。

## 参数调整

```python
ncc_result_json = calculate_coarse_ncc(
    calibration_json,
    coarse_json,
    match_step_um=None,       # 默认等于标定步距；也可显式给正数，单位 um
    min_ncc=0.9,             # 最佳相关性下限
    min_points=5,            # 每个候选至少 5 对数据；允许设置为 >=3 的整数
    min_overlap_ratio=0.6,   # 同时至少覆盖 60% 的粗扫数据
    min_ncc_gap=0.02,        # 与相隔至少一个粗扫步距的另一候选的分数差下限
    max_peak_valley_ratio=0.05,  # 允许同一宽峰顶内浅凹陷占标定极差的最大比例
)
```

这些是待现场验证的初始工程阈值，不保证所有工件适用。至少采集 5 帧，实际应尽量
覆盖峰两侧；不能为了让 `valid` 变 true 就一味降低阈值。
匹配步距比标定步距更小只是在插值曲线上细分，不会增加真实的标定信息。
峰顶有多个最大值时，中点也不是唯一实测最佳焦点，精扫应覆盖 `predicted_peak_range_um`；
仍须检查 `valid`。允许宽峰顶参与 NCC 不会降低 `min_ncc` 或取消边界、歧义检查。
纯 Python 的计算量随候选数量和粗扫点数增长；候选超过 100000 会报错。

标定与粗扫应使用相同清晰度算法、对应 ROI 和一致的成像配置。
明显的形状变化、过稀采样、噪声或多峰仍可能造成误判，最终位置需要精扫/补拍验证。
脚本不能识别丢帧或重复执行，也不检查机械安全行程，调用方需要保证采样同步和运动边界。

## 本地验证

运行 `python -B -m unittest test.test_va_coarse_ncc -v`。
覆盖已知正负位移、幅值缩放和偏置、不同网格插值、反向扫描、小数位置、边缘峰、
重复形状、平坦曲线、数据损坏及不完整采集。没有连接实际 VA 或运动硬件。

2026-08-31 增加用户现场数据回归：标定峰顶 10550～10620 um 有 12 个最大值 84，
中间最低 83，采用代表中点 10585 um。继续计算后最高 NCC 约 0.6521，低于 0.9，
返回 `low_ncc` 和 `valid=false`。不能因为消除了重复峰错误就把本例当作成功匹配。
