#!/usr/bin/env bash
# 关停本项目裁判 vLLM（Qwen3.8）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/run/judge/serve.pid"
PORT="${PORT:-8091}"

stop_pid() {
  local pid="$1"
  if kill -0 "$pid" 2>/dev/null; then
    echo "SIGTERM → pid=$pid"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "SIGKILL → pid=$pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE")"
  stop_pid "$PID"
  pkill -P "$PID" 2>/dev/null || true
  rm -f "$PID_FILE"
fi

if command -v ss >/dev/null 2>&1; then
  EXTRA_PIDS=$(ss -lntp "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u || true)
  for p in $EXTRA_PIDS; do
    echo "端口 $PORT 仍占用 pid=$p"
    stop_pid "$p"
  done
fi

pkill -f "vllm serve /kwkj-k8s/zwb/model/Qwen3.8-27B" 2>/dev/null || true
echo "裁判服务已关停"
