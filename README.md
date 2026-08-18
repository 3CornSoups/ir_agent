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
export MINIMAX_API_KEY=...          # 云端出片时需要；使用本地 MiniMaxH3 时可不填（见下）

# 方式二：复制示例配置
cp configs/gemini.yaml.example configs/gemini.yaml
cp configs/h3.yaml.example configs/h3.yaml
# 编辑上述 yaml 填入密钥（勿提交到 git）
```

仓库内 `configs/gemini.yaml` / `configs/h3.yaml` 的 `api_key` 为空；密钥只放本地或环境变量。

## 本地 MiniMaxH3（可选）
如果你有一个本地的 MiniMax-H3 服务（HTTP 接口），且不需要鉴权，可以：
- 把 `configs/h3.yaml` 的 `base_url` 指向本地服务（例如 `http://127.0.0.1:xxxx`）
- 设置 `skip_auth: true`
- 或直接设置环境变量 `export H3_SKIP_AUTH=true`
- 如本地服务接口路径不是默认 `/v2/video_generation` / `/v2/query/video_generation/{task_id}`，可配置 `generate_path` / `query_path_template`

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

## 可选：对比本地/官方提示词 + 视频（report 里会自动包含 diff）
如果你希望除了生成 `out_local.mp4` 之外，再基于 `official_prompt(raw)` 额外生成 `out_official.mp4`，可以加 `--compare-video`：
```bash
python3 scripts/run.py -m t2va --intent "一只橘猫在窗台晒太阳" --compare-video
```

输出在 `--out-dir`（默认 `runs/<mode>_<时间>/`）：

| 文件 | 内容 |
| --- | --- |
| `prompt.txt` | 本地优化后的最终字段（cleaned） |
| `prompt_official_raw.txt` | 官方/raw 提示词（未清洗） |
| `expanded.txt` | 第一次扩写稿 |
| `inventory.txt` | i2va/r2va 的参考理解 |
| `run.json` | 元数据 |
| `out_local.mp4` | 成片（未加 `--no-video` 时，基于 local prompt） |
| `out_official.mp4` | （可选）成片（加 `--compare-video` 时，基于 official/raw prompt） |
| `report.json` / `report.md` / `prompt_diff.txt` | 提示词对比报告（总是生成） |

出片走 MiniMax `/v2/video_generation`；画幅 `--ratio`、分辨率 `--resolution` 只进视频 API。

## 测试（本地离线）
```bash
pip install -r test/requirements.txt
./test/run_tests.sh
```
