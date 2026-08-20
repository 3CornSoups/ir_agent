# ir_agent 批量运行报告

**运行时间**：2026-08-20T05:40:57Z
**总用例**：4　**通过**：4　**失败**：0　**总耗时**：46.0s

---

## 用例明细

| 状态 | ID | 来源 | 模式 | 耗时 | 质量校验 | 输出目录 | 问题 |
| ---- | -- | ---- | ---- | ---- | -------- | -------- | ---- |
| OK | `t2va_city_rain` | 本地 | t2va | 13.0s | passed | `t2va_20260820_134011` | — |
| OK | `t2va_ocean` | 本地 | t2va | 11.6s | passed | `t2va_20260820_134024` | — |
| OK | `t2va_dance` | 本地 | t2va | 11.6s | passed | `t2va_20260820_134036` | — |
| OK | `t2va_forest` | 本地 | t2va | 9.9s | passed | `t2va_20260820_134047` | — |

---

## 提示词预览

> 以下为各用例生成的 `prompt.txt` 前 400 字预览，完整内容请查看对应的输出目录。

### [OK] t2va_city_rain
- **模式**：t2va
- **意图**：城市夜雨，霓虹倒映在积水路面，一个撑伞的女孩穿过人行横道
- **提示词文件**：`D:\论文和代码项目\代码\ir_agent\runs\t2va_20260820_134011\prompt.txt`

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic documentary style, a tight, waist-level tracking shot follows a girl in a dark mid-calf trench coat walking across a rain-slicked asphalt crosswalk. The camera tracks her movement as she holds a translucent umbrella steady, with deep blue and black tones punctuated by vibrant pink and cyan neon reflections on the wet ground. The...
```

### [OK] t2va_ocean
- **模式**：t2va
- **意图**：无人机俯瞰太平洋日落，橙红色的光芒从地平线铺满海面，远处有一艘孤帆
- **提示词文件**：`D:\论文和代码项目\代码\ir_agent\runs\t2va_20260820_134024\prompt.txt`

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a wide-angle, top-down aerial shot looks directly onto the vast, deep indigo and charcoal surface of the Pacific Ocean. A vibrant, shimmering path of molten-red and orange light stretches from the horizon toward the center of the frame, intensifying as it catches the crests of rolling swells. A small white sailboat with a...
```

### [OK] t2va_dance
- **模式**：t2va
- **意图**：霓虹灯舞台上，一名街舞少女随着强劲节拍做 Bboy 旋转，观众挥舞荧光棒
- **提示词文件**：`D:\论文和代码项目\代码\ir_agent\runs\t2va_20260820_134036\prompt.txt`

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a wide-angle shot captures a vast, darkened stage illuminated by vertical neon magenta and cyan light bars. In the center, a street-dance girl wearing an oversized, textured streetwear hoodie and baggy cargo pants stands in a sharp silhouette. The camera pulls back rapidly with large amplitude as a heavy, bass-boosted breakbeat...
```

### [OK] t2va_forest
- **模式**：t2va
- **意图**：深秋森林，阳光穿过金黄落叶，一只梅花鹿缓步走向溪边饮水，远处有雾气
- **提示词文件**：`D:\论文和代码项目\代码\ir_agent\runs\t2va_20260820_134047\prompt.txt`

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a low-angle tracking shot follows a Sika deer with a sleek chestnut-brown coat and white spots as it moves through a dense autumn forest. The camera pans right with small amplitude at slow speed to keep the deer centered as it walks across a thick carpet of brittle golden-brown maple and oak leaves. Hazy, vertical shafts of...
```
