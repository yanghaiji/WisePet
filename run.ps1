# 一键运行（Windows）：创建 venv -> 安装依赖 -> 启动桌宠
# 依据规约§1：venv 与中间产物均在项目目录内
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
& ".venv\Scripts\python.exe" main.py
