#!/usr/bin/env bash
# 双卡启动本地裁判模型 Qwen3.8-27B（OpenAI 兼容 /v1）。
# 默认 GPU 0,1，端口 8091，served-model-name=qwen3.8
# 用法：
#   ./scripts/judge_serve.sh
#   CUDA_VISIBLE_DEVICES=2,3 PORT=8093 ./scripts/judge_serve.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VLLM_HOME="${VLLM_HOME:-/kwkj-k8s/envs/vllm_19}"
export PATH="${VLLM_HOME}/bin:${PATH}"
export VIRTUAL_ENV="${VLLM_HOME}"

MODEL="${VLLM_MODEL:-/kwkj-k8s/zwb/model/Qwen3.8-27B}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8091}"
TP_SIZE="${TP_SIZE:-2}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1}"
GPU_MEMORY_UTIL="${GPU_MEMORY_UTIL:-0.90}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-16}"
SERVED_NAME="${SERVED_MODEL_NAME:-qwen3.8}"
GDN_PREFILL_BACKEND="${GDN_PREFILL_BACKEND:-triton}"
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-0}"

mkdir -p run/judge
PID_FILE="$ROOT/run/judge/serve.pid"
LOG_FILE="$ROOT/run/judge/serve.log"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "裁判服务已在运行 pid=$(cat "$PID_FILE")，先 ./scripts/judge_stop.sh"
  exit 1
fi

if [[ ! -f "$MODEL/config.json" ]]; then
  echo "模型缺失: $MODEL"
  exit 1
fi

if ! command -v vllm >/dev/null 2>&1; then
  echo "未找到 vllm：$VLLM_HOME"
  exit 1
fi

PREFIX_ARGS=()
if [[ "$ENABLE_PREFIX_CACHING" == "1" ]]; then
  PREFIX_ARGS+=(--enable-prefix-caching)
fi

echo "启动 Qwen3.8-27B 裁判服务"
echo "  model=$MODEL"
echo "  name=$SERVED_NAME"
echo "  GPUs=$CUDA_VISIBLE_DEVICES TP=$TP_SIZE"
echo "  http://127.0.0.1:${PORT}/v1"
echo "  log=$LOG_FILE"

nohup vllm serve "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --dtype bfloat16 \
  --tensor-parallel-size "$TP_SIZE" \
  --max-model-len "$MAX_MODEL_LEN" \
  --max-num-seqs "$MAX_NUM_SEQS" \
  --gpu-memory-utilization "$GPU_MEMORY_UTIL" \
  --trust-remote-code \
  --gdn-prefill-backend "$GDN_PREFILL_BACKEND" \
  --served-model-name "$SERVED_NAME" \
  "${PREFIX_ARGS[@]}" \
  >"$LOG_FILE" 2>&1 &

echo $! >"$PID_FILE"
echo "pid=$(cat "$PID_FILE") 已后台启动"
echo "就绪检测: curl -s http://127.0.0.1:${PORT}/v1/models"
echo "日志: tail -f $LOG_FILE"
