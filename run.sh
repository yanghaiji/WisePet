#!/usr/bin/env bash
# 一键运行（macOS/Linux）：创建 venv -> 安装依赖 -> 启动桌宠
# 依据规约§1：venv 与中间产物均在项目目录内
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python main.py
