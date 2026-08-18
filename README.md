# Gemini → MiniMax-H3 Agent（t2va / i2va / r2va）

短意图先扩写，再整理成官方字段；画幅、分辨率、时长只走视频 API，不写进 prompt。

## 管线

| 模式 | HTTP 次数 | 步骤 |
| --- | --- | --- |
| t2va | 2 | 扩写意图 → 共用格式化 |
| i2va | 3 | Flash Lite 看首帧 → 扩写（图描述+意图）→ 共用格式化 |
| r2va | 3 | 看参考图/视频/音频 → 扩写 → 共用格式化 |

三模式最后一步共用 `prompts/format_h3.txt`，按 `MODE=` 切换输出骨架：

- t2va / i2va：三字段（i2va 多一行 Picture 1 对齐句）
- r2va：六段（subject_definitions … non_diegetic_music）

## 配置

```bash
pip install -r requirements.txt

# 方式一：环境变量（推荐）
export GEMINI_API_KEY=...
export GEMINI_ENDPOINT=...          # Cloudsway 网关 endpoint
export MINIMAX_API_KEY=...          # 出片时需要

# 方式二：复制示例配置
cp configs/gemini.yaml.example configs/gemini.yaml
cp configs/h3.yaml.example configs/h3.yaml
# 编辑上述 yaml 填入密钥（勿提交到 git）
```

仓库内 `configs/gemini.yaml` / `configs/h3.yaml` 的 `api_key` 为空；密钥只放本地或环境变量。

## 调用

```bash
cd /kwkj-k8s/zwb/项目/agent/new_agent0818

# 只出 prompt
python3 scripts/run.py -m t2va --intent "一只橘猫在窗台晒太阳" --no-video

# t2va 出片（16:9 只进 API）
python3 scripts/run.py -m t2va --intent "雨夜涩谷，红巴士穿过路口，环境音，无对白" \
  --duration 6 --ratio 16:9 --out-dir runs/neon

# i2va
python3 scripts/run.py -m i2va --intent "人物向前走，镜头缓推" \
  --first-frame /path/to/first.png --duration 5 --no-video

# r2va
python3 scripts/run.py -m r2va --intent "保持人设，在街道上走路" \
  --ref-image face.png --ref-video walk.mp4 --duration 5 --no-video
```

输出在 `--out-dir`（默认 `runs/<mode>_<时间>/`）：

| 文件 | 内容 |
| --- | --- |
| `prompt.txt` | 喂 H3 的最终字段 |
| `expanded.txt` | 第一次扩写稿 |
| `inventory.txt` | i2va/r2va 的参考理解 |
| `run.json` | 元数据 |
| `out.mp4` | 成片（未加 `--no-video` 时） |

出片走 MiniMax `/v2/video_generation`；画幅 `--ratio`、分辨率 `--resolution` 只进视频 API。
