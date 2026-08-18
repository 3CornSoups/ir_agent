#!/usr/bin/env bash
# 在 test/ 目录内运行全部单元测试。
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pytest -c pytest.ini "$@"
