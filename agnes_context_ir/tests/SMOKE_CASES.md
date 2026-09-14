# 冒烟用例（对白 / 运镜）

图片目录：`tests/assets/`

| ID | 模式 | 意图要点 | 期望 |
|----|------|----------|------|
| 01 | t2va | 不要说话 + 固定机位 | 无 `<d>` |
| 02 | t2va | 咖啡馆聊天但没写台词 + 缓慢推近 | 应出现 `<d>` |
| 03 | t2va | 明确台词「末班车还有三分钟」+ 小幅 pan | 保留原句 |
| 04 | i2va | 首帧 `cafe_woman.jpg` + 「你来啦」+ 跟拍 | 保留原句 + 对齐句 |
| 05 | i2va | 首帧 `library.jpg` + 禁言 + 克制运镜 | 无 `<d>` |

运行：

```bash
cd agnes_context_ir
python tests/smoke_five_cases.py
```
