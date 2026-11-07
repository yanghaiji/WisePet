"""素材规格闸：皮肤 × 10 动作 × 6 帧、256×256 RGBA、帧间互异。"""
from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("wisepet_verify_assets",
                                              ROOT / "tools" / "verify_assets.py")
verify_assets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_assets)


def _png(path: Path, *, size=256, color=6, token: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR"
              + struct.pack(">IIBBBBB", size, size, 8, color, 0, 0, 0))
    path.write_bytes(header + token)


def _full_skin(base: Path, skin: str = "cat") -> None:
    for action in verify_assets.ACTIONS:
        for index, frame in enumerate(verify_assets.FRAMES):
            _png(base / skin / action / frame, token=f"frame-{action}-{index}".encode())


def should_pass_a_complete_skin(tmp_path):
    _full_skin(tmp_path)
    assert verify_assets.verify(tmp_path)["total"] == 0


def should_report_missing_frames(tmp_path):
    _full_skin(tmp_path)
    (tmp_path / "cat" / "idle" / "03.png").unlink()
    problems = verify_assets.verify(tmp_path)["problems"]["cat"]
    assert any("缺帧" in item for item in problems)


def should_report_wrong_size_and_non_rgba(tmp_path):
    _full_skin(tmp_path)
    _png(tmp_path / "cat" / "walk" / "01.png", size=128)
    _png(tmp_path / "cat" / "walk" / "02.png", color=2)
    problems = "\n".join(verify_assets.verify(tmp_path)["problems"]["cat"])
    assert "尺寸" in problems and "非 RGBA" in problems


def should_report_an_action_that_copies_one_frame_six_times(tmp_path):
    _full_skin(tmp_path)
    for frame in verify_assets.FRAMES:
        _png(tmp_path / "cat" / "eat" / frame, token=b"same")
    problems = "\n".join(verify_assets.verify(tmp_path)["problems"]["cat"])
    assert "重复字节" in problems


def should_only_check_the_requested_skin(tmp_path):
    _full_skin(tmp_path, "cat")
    _full_skin(tmp_path, "dog")
    result = verify_assets.verify(tmp_path, skin="cat")
    assert result["skins"] == ["cat"]
