# agnes_context_ir 瘦身管线设计

**日期:** 2026-09-07  
**状态:** 已批准并执行

## 目标

在**不更换外部接口**（`enhance` / `run_job` / `AGNES_*` 环境变量）的前提下：

1. 去掉 skills / T8 机制路由等多轮 HTTP，对齐 `latest_agent` 的 2/3 轮编排以提速。
2. t2va 走文本模型；所有 Gemini 调用关闭思考模式。
3. 修正台词与镜头策略：禁言则禁止对白；对话情景缺台词则补 1～2 句；未指定运镜则克制默认。

## 架构

- 对外：签名与参数兼容；`skills` / `skill_router` / `mechanisms` / `mechanism_router` 接受但忽略。
- 对内：
  - t2va：R1 keyinfo → R2 format（2 次，文本模型）
  - i2va/fl2va/l2va：R1 image_guide → R2 format（2 次）
  - r2va：R1 perceive_fuse → R2 plot → R3 format（3 次）
- 本地后处理：去 MODE=/markdown、画幅泄漏、对齐句补全；不做 LLM verify 重写。
- 返回字段兼容：旧键保留，空值或映射填充。

## 配置

- `AGNES_GEMINI_MODEL`：多模态默认模型
- `AGNES_GEMINI_TEXT_MODEL`：t2va 文本模型（未设则回退前者）
- 思考模式默认关闭（请求体写死，可后续加 env 覆盖）

## 非目标

- 不改产线调用方签名
- 已删除 `skills/`、风格/T8 路由与旧多阶段 LLM 模块；保留 `h3-prompt-writing/` 官方指南
