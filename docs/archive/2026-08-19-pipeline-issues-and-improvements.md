# 管线问题根因分析与改进方案

> 日期：2026-08-19
> 背景：通过 Agent 优化意图 vs 官方优化意图的对比测试，发现以下三类问题。

---

## 一、问题现象

1. **简单短视频（单镜头、少情节）时 Agent 出片质量反而更差**
2. **物理规则表现不自然**（运动、布料、流体等）
3. **没有完全按参考图生成**（如参考图有衣服，生成视频里衣服消失）

---

## 二、底层根因分析

### 问题一：简单短视频的过度膨胀

**根本原因：管线有一个固定的"膨胀偏置"，无法随内容复杂度自适应收缩。**

**层次 1 — `expand_intent` 强制往任何意图里加细节**

`expand_intent.txt` 里写了：
> "Add only details that serve the intent: materials, lighting, action physics, ambient sound"

但这只是软约束。Gemini 面对一句简单意图时，"serve the intent" 的边界是模糊的，模型倾向于把 5 个句子的意图扩写成 15 个句子。

**层次 2 — `elaborate` 再做一轮扩充**

`elaborate.txt` 里的目标是：
> "matching the detail level of the official H3-Context-IR output"

官方 Context-IR 示例是面向**复杂内容**设计的详略级别。一个 4 秒单镜头的意图被强制对齐到这个详略级别，就会出现大量"为了填充而填充"的描述（无必要的微表情、背景层次、道具材质）。

**层次 3 — `format_h3` system prompt 里有一句话直接造成问题**

```
"A 5-second single-shot clip should read like a full shot-by-shot production description."
```

这句话对简单意图是反效果的：它让模型在单镜头上堆砌多层描述，信息密度超出 H3 模型实际能处理的范围，导致模型在执行时"注意力分散"，物理运动逻辑就崩了。

---

### 问题二：物理规则描述失控

**根本原因：扩写稿里包含 H3 无法执行的"物理精度"描述，且这些描述在 `elaborate` 阶段被进一步具体化。**

`elaborate.txt` 里明确要求加：
> "micro-motions and facial expressions"、"lighting source, direction, and quality (hard/soft)"

H3 生成模型对这类精确物理量不敏感，无法精确控制布料物理、流体、次表面散射。但 elaborate 生成的提示词里会写出类似"丝质布料在 0.8m/s 侧风中产生的柔和褶皱波动"这样 H3 完全无视的描述。

**更深的问题**：这些 H3 无视的精细描述会**稀释真正有效的控制词的权重**。H3 模型的注意力是有限的，当提示词里 30% 是无法执行的物理细节时，真正重要的动作描述所得到的权重就下降了。

---

### 问题三：参考图属性（衣着等）在传递中衰减

**这是整个管线中最严重的结构性问题，有一条明确的信息衰减链：**

```
感知 (perceive_image)
  → 库存文本 (inventory)
    → expand_intent  ← 衰减点 1
      → elaborate    ← 衰减点 2
        → format_h3  ← 衰减点 3（约束力已弱）
          → H3 生成  ← 模型按自己的先验填充丢失的细节
```

**衰减点 1：`expand_intent`**

`_expand_user()` 把 inventory 作为文本参数传入，但 `expand_intent.txt` 的 system prompt 里只有：
> "If a reference inventory is provided, stay consistent with it."

"stay consistent" 是宽泛的语义约束，不是视觉属性的强制保留。Gemini 在把库存转写为扩写散文时会自然语言化，把"米白色长款外套，纽扣排列，翻领，内搭黑色高领毛衣"压缩成"穿着得体的白色外套"，甚至完全省略（因为它认为这是"已知的"）。

**衰减点 2：`elaborate`**

`_elaborate_user()` 传入的是已经衰减过的 `expanded` 散文，以及原始 `inventory`。但 elaborate 的 system prompt 里没有任何"严格保留 inventory 中的每一个视觉属性"的指令，只说：
> "Do not invent reference assets; stay consistent with any inventory provided"

这和 expand 的约束一样弱。

**衰减点 3：`format_h3`**

格式化阶段虽有：
> "Keep identity/layout from the first frame"（仅 i2va 模式的提示）

但这只针对 i2va，且只说 "keep identity/layout"，没有具体到服装、配饰、颜色这些关键属性。对于 r2va 和 fl2va，没有类似的强制性措辞。

**`verify` 层的盲区**

`verify.py` 里的所有 check 函数都是结构性校验（字段存在性、时间戳单调性、标签编号等），**完全没有语义层面的视觉一致性检查**。即使衣服在传递中完全丢失，校验也通过了。

---

## 三、改进方案

### 方案 A：解决简单内容的过度膨胀

**改动位置：`elaborate.txt`、`format_h3.txt`**

在 `elaborate.txt` 里加入自适应缩放指令：

> "Match detail depth to scene complexity. A single-shot 4-second clip with one continuous motion needs one paragraph covering composition, subject, action, camera, and sound — not the same length as a multi-shot sequence. Do NOT pad with micro-details that cannot be executed (sub-pixel lighting, precise fabric physics, millimeter-scale motions)."

在 `format_h3.txt` 里把反效果的那句话改掉：

- 当前：`"A 5-second single-shot clip should read like a full shot-by-shot production description."`
- 改为：`"A 5-second single-shot clip needs clear subject + action + environment + sound. Do not pad with unexecutable physical micro-details."`

---

### 方案 B：解决物理规则描述失控

**改动位置：`elaborate.txt`**

新增硬约束：

> "Do NOT describe physical quantities that video generation models cannot execute: exact wind speeds, precise fabric deformation, sub-surface scattering parameters, specific focal lengths, millimeter-precision motion. Use action-level language: 'the skirt ripples in the breeze', not 'the 72g/m² silk fabric deforms at 0.6m/s lateral airflow'."

---

### 方案 C：解决参考图属性衰减（最核心）

**C1：在 `expand_intent.txt` 和 `elaborate.txt` 里加视觉属性锁定指令（成本最低、效果最直接）**

> "VISUAL IDENTITY LOCK — if a reference inventory is provided, every explicitly described visual attribute of a subject (clothing item type, color, coverage level, texture, accessories, hairstyle) MUST be preserved verbatim or paraphrased with equivalent specificity. You MAY NOT omit, generalize, or replace any attribute. If the inventory says 'white long coat, buttoned, lapel collar, black turtleneck underneath', the output must carry all of these."

**C2：在 `format_h3` 的 `_format_user()` 里提取关键视觉属性做硬约束注入**

目前 `_format_user()` 只是把 inventory 原文附在后面。改造方向：解析 inventory 里的视觉属性，构造一个明确的"不得修改"清单，显式注入到 format user 里。

```python
def _extract_visual_lock(inventory: str) -> str:
    """从库存里提取需要强制保留的视觉属性，构造锁定块。"""
    # 可以用 Gemini 做一次专项提取，或用规则匹配 clothing/costume/appearance 段落
    ...
```

**C3：在 `verify.py` 里增加视觉一致性语义检查（兜底安全网）**

目前 verify 只做结构校验。新增：

```python
def check_visual_consistency(prompt: str, inventory: str | None) -> list[VerifyIssue]:
    """检查最终提示词是否遗漏了 inventory 中的关键视觉属性（衣着/颜色/配饰）。"""
    # 用 LLM 做语义对比，专门检查衣着/颜色/配饰是否完整保留
    ...
```

---

## 四、优先级排序

| 优先级 | 方案 | 改动位置 | 成本 | 效果 | 状态 |
|--------|------|----------|------|------|------|
| 最高 | C1：扩写 prompt 加视觉属性锁定 + 禁止降覆盖 | `expand_intent.txt`、`elaborate.txt`、`format_h3.txt` | 低（只改文本） | 直接截断最主要的衰减 | **C1+A 已实施**（2026-08-20） |
| 最高 | C1-pre：感知必须写清衣着与覆盖 | `perceive_image.txt`、`perceive_refs.txt` | 低 | 避免库存阶段就丢衣服 | **已实施** |
| 高 | A：elaborate/format 自适应缩放 | `elaborate.txt`、`format_h3.txt` | 低 | 解决简单内容过度膨胀 | **已实施** |
| 高 | B：禁止不可执行物理描述 | `elaborate.txt`、`expand_intent.txt` | 低 | 改善物理逻辑 | **已并入 A** |
| 中 | C3：verify 层加视觉一致性检查 | `verify.py` | 中 | 兜底安全网 | 未做 |
| 中 | C2：format 阶段提取视觉锁定块 | `pipeline.py` | 中 | 最后一道保证 | 未做 |

---

## 五、总结

三类问题的本质：

- **过度膨胀**：管线缺少"复杂度感知"，对简单内容施加了与复杂内容相同的扩写压力。
- **物理失真**：elaborate 阶段追求细节密度，但未区分"可执行的动作语言"与"无法执行的物理量"。
- **视觉属性衰减**：inventory → expand → elaborate → format 是一条有损的文本传递链，每一步的 "stay consistent" 都是软约束，在散文改写中自然丢失细节；verify 层没有语义守门。

最关键的一条改进是 **C1**：在两个扩写 prompt 里加 VISUAL IDENTITY LOCK，成本极低但可以切断衰减链上最大的漏洞。
