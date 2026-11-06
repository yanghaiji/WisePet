"""测试环境装配：src 布局导入 + offscreen Qt 平台（ui 冒烟无需显示环境）。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
