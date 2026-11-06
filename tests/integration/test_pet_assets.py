"""仓库内桌宠素材完整性：assets/pet/{skin}/{action}/ 每动作 10 帧真实 PNG（PRD v2.0 C11）。

素材有两条来路：tools/gen_pet_assets.py 程序生成，或 tools/import_pet_images.py
导入外部 AI 图。本测试只校“已落盘素材”的契约（不关心动画长什么样），按已安装皮肤动态取参，
核心动作齐全的皮肤会被自动纳入校验，未生成的皮肤不报错。
不 import PySide6（integration 层铁律）：用 PNG 魔数与 IHDR 头做可解码校验；
可复现性则通过子进程重跑生成器比对，测试自身不引 PySide6。
"""
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from WisePet.domain.actions import ACTION_NAMES
from WisePet.domain.config_model import DEFAULT_SKIN, PET_SKINS
from WisePet.infra.frame_loader import ACTIONS, FrameAssetLoader

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "assets"
FRAMES_PER_ACTION = 10
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: 正常帧约 10~30KB；低于此值基本可判定为截断/空文件
MIN_FRAME_BYTES = 1000
#: 生成器画布边长与 RGBA 颜色类型（IHDR 第 25 字节）：透明背景必须有 alpha 通道
CANVAS_PX = 256
RGBA_COLOR_TYPE = 6
GENERATOR = ASSETS_ROOT.parent / "tools" / "gen_pet_assets.py"

#: 收集期扫描一次：素材齐全的皮肤（默认皮肤永远必须在内）
INSTALLED_SKINS = FrameAssetLoader(ASSETS_ROOT).available_skins()


def _ihdr(raw: bytes) -> tuple[int, int, int]:
    """从 PNG 头取（宽, 高, 颜色类型）：魔数 8 字节 + IHDR 长度/类型 8 字节 = 数据从 16 起。"""
    width, height = struct.unpack(">II", raw[16:24])
    return width, height, raw[25]


def should_ship_default_skin_when_repository_installed():
    assert DEFAULT_SKIN in INSTALLED_SKINS
    assert set(INSTALLED_SKINS) <= set(PET_SKINS)


def should_ship_ten_actions_in_source_of_truth():
    """测试参数化源与运行时一致：新动作漏生成时这里会红，而不是静默少校。"""
    assert ACTIONS == frozenset(ACTION_NAMES)
    assert len(ACTION_NAMES) == 10


@pytest.mark.parametrize("skin", INSTALLED_SKINS)
@pytest.mark.parametrize("action", sorted(ACTIONS))
def should_ship_expected_frames_when_assets_generated(skin, action):
    frames = FrameAssetLoader(ASSETS_ROOT, skin).frames(action)

    assert len(frames) == FRAMES_PER_ACTION, f"{skin}/{action}"


@pytest.mark.parametrize("skin", INSTALLED_SKINS)
def should_report_no_missing_action_when_skin_assets_present(skin):
    assert FrameAssetLoader(ASSETS_ROOT, skin).missing_actions() == []


@pytest.mark.parametrize("skin", INSTALLED_SKINS)
@pytest.mark.parametrize("action", sorted(ACTIONS))
def should_keep_frames_decodable_when_committed(skin, action):
    for path in FrameAssetLoader(ASSETS_ROOT, skin).frames(action):
        raw = path.read_bytes()

        assert raw.startswith(PNG_MAGIC), path
        assert len(raw) > MIN_FRAME_BYTES, path


@pytest.mark.parametrize("skin", INSTALLED_SKINS)
@pytest.mark.parametrize("action", sorted(ACTIONS))
def should_keep_canvas_square_with_alpha_when_assets_generated(skin, action):
    """尺寸不一致会让窗口内容偏移，缺 alpha 通道会在桌面带一块白底。"""
    for path in FrameAssetLoader(ASSETS_ROOT, skin).frames(action):
        width, height, color_type = _ihdr(path.read_bytes())

        assert (width, height) == (CANVAS_PX, CANVAS_PX), path
        assert color_type == RGBA_COLOR_TYPE, path


@pytest.mark.parametrize("action", sorted(ACTIONS))
def should_reproduce_identical_bytes_when_regenerated(tmp_path, action):
    """笔触随机数以 `皮肤|动作` 为种子：生成器同参数重跑逐字节一致。

    比的是“生成器自己两次运行”，不是“生成器 vs 已安装素材”：素材可被
    tools/import_pet_images.py 换成 AI 图，那时已落盘本就不该等于生成结果。
    """
    runs = []
    for tag in ("first", "second"):
        out = tmp_path / tag
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--skin", DEFAULT_SKIN,
             "--action", action, "--out", str(out)],
            capture_output=True, text=True, timeout=120, check=False,
        )

        assert result.returncode == 0, result.stderr
        runs.append(out)

    for name in (f"{i + 1:02d}.png" for i in range(FRAMES_PER_ACTION)):
        left = (runs[0] / "pet" / DEFAULT_SKIN / action / name).read_bytes()
        right = (runs[1] / "pet" / DEFAULT_SKIN / action / name).read_bytes()

        assert left == right, name


def should_skip_imported_action_when_regenerating(tmp_path):
    """生成器不能静默盖掉外部导入的 AI 图：看到 `.imported` 标记就跳过该动作。

    实测踩过：带 --preview 跑 `--skin cat` 全皮肤，三个 AI 动作被程序帧盖回去。
    这里预先放一个标记文件，断言重生不碰它。
    """
    out = tmp_path / "assets"
    imported = out / "pet" / DEFAULT_SKIN / "idle"
    imported.mkdir(parents=True)
    (imported / ".imported").write_text("docs/images", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--skin", DEFAULT_SKIN,
         "--action", "idle", "--out", str(out)],
        capture_output=True, text=True, timeout=120, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "跳过" in result.stdout, result.stdout
    assert not (imported / "01.png").exists()   # 外部帧未被重写
