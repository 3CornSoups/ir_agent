# eval100 全量测评报告（Qwen3.8 双卡裁判）

日期：2026-08-25  
裁判：`qwen3.8` @ `http://127.0.0.1:8091`（`/kwkj-k8s/zwb/model/Qwen3.8-27B`，GPU 0,1 TP=2）  
样例：`input/eval100_intents.txt`（100 条 t2va）  
产物：`runs/eval100_full_20260825/`

## 总结果

| 指标 | 值 |
| --- | --- |
| overall_mean | **4.43** / 5 |
| 低分 case（<3.5） | 2 / 100 |
| 失败 enhance | 0 |

## 最弱维度（改前全量）

1. d14 编辑可控 4.07  
2. d04 角色一致性 4.11  
3. d11 多主体 4.12  
4. d18 文字字幕 4.14  
5. d08 空间 4.25  

高频 issue_tags：`invented_assets`、`invented_text`、`identity_drift`、`style_mismatch`

## 根因与已落地修复

| 问题 | 根因 | 修复 |
| --- | --- | --- |
| 纯「手绘速写」被改成实拍融合 | `handdrawn-live` 触发词含「手绘」 | 收紧 catalog 触发词 + 路由说明；用户风格优先于 skill |
| 动作链被压成高潮瞬间 | expand 未强制保留链式动词 | INTENT SPINE：停球→转身→射门全保留 |
| 幻觉字幕/道具 | 缺 ANTI-INVENTION | expand/format 禁止未请求的屏上字与素材 |
| 未引号中文口号变英文占位 | 占位写成 Brand Slogan | 未引号也必须短中文，禁止英文占位 |

## 回归（2 条低分 case）

| case | 改前 overall | 改后 overall |
| --- | --- | --- |
| c085 手绘速写 | 3.3 | **4.2**（d13=5） |
| c078 停球转身射门 | 3.4 | **4.2** |

## 复现命令

```bash
# 启动裁判
./scripts/judge_serve.sh

# 100 条测评
python3 scripts/batch_eval100.py --out-root runs/eval100_full_YYYYMMDD

# 看报告
cat runs/eval100_full_*/analysis.md
```
