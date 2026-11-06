"""AI 素材导入器的契约校验：命令行走 exit code，落盘形状按 PNG 头验。

与 test_pet_assets.py 同一口径：integration 层不 import PySide6，
所以用 zlib+struct 手搓最小 PNG 编码器造输入图，用 IHDR 读输出图。
"""
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "import_pet_images.py"
SIZE = 256
RGBA_COLOR_TYPE = 6
FRAMES_PER_ACTION = 6


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _write_rgb_png(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    """color type 2（不带 alpha）：模拟多数 AI 工具默认输出的实底图。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + row for row in rows)
    png = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
           + _chunk(b"IDAT", zlib.compress(scanlines)) + _chunk(b"IEND", b""))
    path.write_bytes(png)


def _sheet(cell: int, blob: tuple[int, int, int], color: tuple[int, int, int]) -> list[bytes]:
    """一张实底小图：白底 + 一个色块（single 布局的输入）。"""
    rows = []
    for y in range(cell):
        row = bytearray([255] * (cell * 3))
        for x in range(cell):
            if blob[0] <= x < blob[0] + blob[2] and blob[1] <= y < blob[1] + blob[2]:
                row[x * 3:x * 3 + 3] = bytes(color)
        rows.append(bytes(row))
    return rows


def _grid_sheet(width: int, height: int, rows: int, cols: int,
                blobs: list[tuple[int, int, int]]) -> list[bytes]:
    """把每格画成不同大小/位置的色块：模拟 AI 每帧站位都不一样的真实情况。"""
    cw, ch = width // cols, height // rows
    canvas = [bytearray([255] * (width * 3)) for _ in range(height)]
    for i, (bx, by, side) in enumerate(blobs):
        r, c = divmod(i, cols)
        ox, oy = c * cw + bx, r * ch + by
        for y in range(oy, min(oy + side, (r + 1) * ch)):
            for x in range(ox, min(ox + side, (c + 1) * cw)):
                canvas[y][x * 3:x * 3 + 3] = bytes((60, 122, 60))
    return [bytes(row) for row in canvas]


def _ihdr(raw: bytes) -> tuple[int, int, int]:
    width, height = struct.unpack(">II", raw[16:24])
    return width, height, raw[25]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, timeout=180, check=False)


@pytest.fixture
def grid_src(tmp_path):
    """造 2 个动作 × 6 帧的网格图（2×3），每格色块大小与站位都不同。"""
    src = tmp_path / "ai"
    src.mkdir()
    blobs = [(4, 20, 18), (10, 6, 26), (2, 14, 22), (16, 18, 14), (8, 8, 30), (20, 24, 10)]
    for action in ("idle", "walk"):
        _write_rgb_png(src / f"{action}.png", 120, 120, _grid_sheet(120, 120, 2, 3, blobs))
    return src


def should_import_grid_sheet_into_six_square_rgba_frames(grid_src, tmp_path):
    out = tmp_path / "assets"

    result = _run(["--in", str(grid_src), "--skin", "cat", "--out", str(out)])

    assert result.returncode == 0, result.stdout + result.stderr
    for action in ("idle", "walk"):
        directory = out / "pet" / "cat" / action
        names = sorted(p.name for p in directory.glob("*.png"))
        assert names == [f"{i:02d}.png" for i in range(1, FRAMES_PER_ACTION + 1)]
        for path in directory.glob("*.png"):
            assert _ihdr(path.read_bytes()) == (SIZE, SIZE, RGBA_COLOR_TYPE), path


def should_warn_but_still_succeed_when_actions_missing(grid_src, tmp_path):
    """只给两个动作也允许导入（loader 有相似回落），但必须把缺的报出来。"""
    result = _run(["--in", str(grid_src), "--skin", "cat",
                   "--out", str(tmp_path / "assets")])

    assert result.returncode == 0
    line = next(l for l in result.stdout.splitlines() if l.startswith("缺少动作素材"))
    reported = line.split("：", 1)[1].split("（")[0]
    assert "run" in reported and "idle" not in reported


def should_sample_six_frames_from_oversized_grid(tmp_path):
    """AI 常出 2×4=8 格：必须等间隔抽成契约的 6 帧，而不是拒收或截前六张。"""
    src = tmp_path / "ai"
    src.mkdir()
    blobs = [(4, 20, 18), (10, 6, 26), (2, 14, 22), (16, 18, 14),
             (8, 8, 30), (20, 24, 10), (6, 16, 24), (14, 10, 16)]
    _write_rgb_png(src / "idle.png", 160, 80, _grid_sheet(160, 80, 2, 4, blobs))
    out = tmp_path / "assets"

    result = _run(["--in", str(src), "--skin", "cat", "--rows", "2", "--cols", "4",
                   "--out", str(out)])

    assert result.returncode == 0, result.stdout + result.stderr
    directory = out / "pet" / "cat" / "idle"
    assert sorted(p.name for p in directory.glob("*.png")) == [
        f"{i:02d}.png" for i in range(1, FRAMES_PER_ACTION + 1)]


def should_reject_grid_shape_with_too_few_cells(grid_src, tmp_path):
    """格数不够是命令行问题（rows/cols 没对准图），归参数错 exit 2。"""
    result = _run(["--in", str(grid_src), "--skin", "cat", "--out", str(tmp_path),
                   "--rows", "1", "--cols", "2"])

    assert result.returncode == 2
    assert "rows×cols" in result.stdout


def should_reject_frames_below_two(grid_src, tmp_path):
    """--frames 1 会播成静图，而且回环差无从定义，直接拒。"""
    result = _run(["--in", str(grid_src), "--skin", "cat", "--out", str(tmp_path),
                   "--frames", "1"])

    assert result.returncode == 2
    assert "--frames" in result.stdout


def should_reject_unknown_skin_and_missing_dir(grid_src, tmp_path):
    bad_skin = _run(["--in", str(grid_src), "--skin", "panda", "--out", str(tmp_path)])
    bad_dir = _run(["--in", str(tmp_path / "nowhere"), "--skin", "cat", "--out", str(tmp_path)])

    assert bad_skin.returncode == 2 and "未知皮肤" in bad_skin.stdout
    assert bad_dir.returncode == 2 and "目录不存在" in bad_dir.stdout


def should_reject_single_layout_with_wrong_frame_count(tmp_path):
    src = tmp_path / "ai"
    (src / "idle").mkdir(parents=True)
    for i in range(3):
        _write_rgb_png(src / "idle" / f"{i + 1:02d}.png", 60, 60,
                       _sheet(60, (10, 10, 20), (60, 122, 60)))

    result = _run(["--in", str(src), "--skin", "cat", "--layout", "single",
                   "--out", str(tmp_path / "assets")])

    assert result.returncode == 1
    assert "每动作需要 6 帧" in result.stdout


def should_fail_when_a_cell_has_no_content(tmp_path):
    """整格纯背景：抠完没内容必须报错，而不是静默写一张全透明的帧。"""
    src = tmp_path / "ai"
    src.mkdir()
    _write_rgb_png(src / "idle.png", 120, 120, _grid_sheet(120, 120, 2, 3,
                                                           [(10, 10, 20)] * 5 + [(0, 0, 0)]))

    result = _run(["--in", str(src), "--skin", "cat", "--out", str(tmp_path / "assets")])

    assert result.returncode == 1
    assert "透明的" in result.stdout
