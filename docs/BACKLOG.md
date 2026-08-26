# BACKLOG

顺手发现、不在当前 commit 处理的问题。

- [ ] `input/judge_gold.jsonl` 中 20 条 `eval100_provisional_same_judge` 需用人工或 Claude Opus 级模型重标，否则 T0.4 ρ 无意义
- [ ] S2 `expected_retain` 部分为模式化占位，应用真实库存属性（红衣/发型等）替换
- [ ] `scripts/blind_ab.py` 的 pairs 清单尚未从母仓 19 条 gold_ir 自动生成
- [ ] gate 非 dry-run 路径需接 `pipeline.enhance` 批量（现需 `--prompts-root`）
- [ ] A8「标语」类与对白引号边界：若意图同时含对白与口号，需更多回归样例
- [ ] skill 路由 keyword 在 S4 negative 上可能误命中——留给 T1.5 阈值曲线
- [ ] S1 `c042`（母亲给孩子系鞋带）Gemini 持续 `PROHIBITED_CONTENT`，暂无法 enhance；当前 S1 F=0.99 仍达标，需安全策略或换模型绕过
- [ ] Gemini 裁判 five_ratio≈0.55（>35%）：需在不破坏 ρ 的前提下压低 5 分通胀（温度/锚点/后处理择一）
