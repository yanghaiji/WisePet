"""素材一致性校验（纯标准库，不依赖 Qt）：皮肤 × 动作 × 帧的规格闸。

桌宠素材曾被反复返工（导入管线剔除装饰、只导 4 个动作、时间戳新旧混着），
这里把「什么算一套合格素材」固化成可跑的检查，补图/换素材后一键目检前置：

    python tools/verify_assets.py                # 报告全部皮肤
    python tools/verify_assets.py --skin cat     # 只看一套
    python tools/verify_assets.py --strict       # 有问题时返回非 0（供 CI 用）

规格：每个皮肤 10 个动作、每动作 6 帧 `01..06.png`、256×256 RGBA、6 帧字节互异。
只读 PNG 头与字节，不解码像素，秒级完成。
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "pet"
ACTIONS = ("idle", "walk", "run", "stretch", "hurt", "think",
           "eat", "speak", "sleep", "sad")
FRAMES = tuple(f"{i:02d}.png" for i in range(1, 7))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FRAME_SIZE = 256


def png_info(path: Path) -> tuple[int, int, int]:
    """返回 (宽, 高, color_type)；6 = RGBA。不解析完整文件。"""
    head = path.read_bytes()[:26]
    if len(head) < 26 or head[:8] != PNG_MAGIC or head[12:16] != b"IHDR":
        raise ValueError("不是合法 PNG 头")
    width, height = struct.unpack(">II", head[16:24])
    return width, height, head[25]


def verify_skin(skin_dir: Path) -> list[str]:
    problems: list[str] = []
    skin = skin_dir.name
    for action in ACTIONS:
        folder = skin_dir / action
        if not folder.is_dir():
            problems.append(f"{skin}/{action}: 缺整个动作目录")
            continue
        digests: list[str] = []
        for frame in FRAMES:
            path = folder / frame
            if not path.is_file():
                problems.append(f"{skin}/{action}/{frame}: 缺帧")
                continue
            try:
                width, height, color_type = png_info(path)
            except (ValueError, OSError) as exc:
                problems.append(f"{skin}/{action}/{frame}: {exc}")
                continue
            if (width, height) != (FRAME_SIZE, FRAME_SIZE):
                problems.append(f"{skin}/{action}/{frame}: 尺寸 {width}x{height}")
            if color_type != 6:
                problems.append(f"{skin}/{action}/{frame}: 非 RGBA（color_type={color_type}）")
            digests.append(hashlib.md5(path.read_bytes()).hexdigest())
        if len(digests) == len(FRAMES) and len(set(digests)) < len(FRAMES):
            problems.append(f"{skin}/{action}: 6 帧里有重复字节（疑似整组复制）")
    return problems


def verify(assets_dir: Path | str = ASSETS, skin: str = "") -> dict:
    """返回 {皮肤: 问题列表} 与总数；便于 CI 与单测直接调用。"""
    base = Path(assets_dir)
    skins = [base / skin] if skin else sorted(p for p in base.iterdir() if p.is_dir())
    report = {s.name: verify_skin(s) for s in skins}
    return {"skins": list(report), "problems": report,
            "total": sum(len(items) for items in report.values())}


def main() -> int:
    parser = argparse.ArgumentParser(description="校验桌宠素材规格")
    parser.add_argument("--skin", default="", help="只校验指定皮肤")
    parser.add_argument("--strict", action="store_true", help="有问题时返回非 0")
    parser.add_argument("--assets", default=str(ASSETS), help="素材根目录")
    args = parser.parse_args()

    result = verify(args.assets, args.skin)
    for skin_name, problems in result["problems"].items():
        if problems:
            print(f"[FAIL] {skin_name}: {len(problems)} 项")
            for item in problems[:12]:
                print(f"  - {item}")
            if len(problems) > 12:
                print(f"  … 另有 {len(problems) - 12} 项")
        else:
            print(f"[ OK ] {skin_name}: {len(ACTIONS)} 动作 × {len(FRAMES)} 帧合格")
    print(f"合计 {result['total']} 项问题")
    return 1 if args.strict and result["total"] else 0


if __name__ == "__main__":
    sys.exit(main())
