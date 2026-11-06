"""程序素材的运动质量：不抖、不重复、不许出现静图。

四条都是实测出来的缺陷回归闸，静态预览一张都看不出来：
① 装饰随机数一旦带上帧号，身体外圈软毛每帧换位置 → 播起来浑身发抖；
② 对称波形让六帧里成对逐字节相同：`sin` 满足 f(t+0.5) == -f(t)，实测 idle 的
   01.png 与 03.png 相同；单峰 `sin` 关于 π/2 对称，实测 stretch 的 2=6、3=5 相同
   → 六帧素材实际只有三四帧，播起来是"顿两下"而不是循环；
③ 呼吸/啄食幅度太小（1.6px@256 = 屏幕 0.7px）→ 与静图无异；
④ frame_diffs 自己也曾错过：把"比相邻两帧"写成按下标取模错位，量出的数与画面
   无关，曾据此把正常素材误判成"浑身发抖"，所以指标本身也必须测。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from WisePet.domain.actions import ACTION_NAMES
from tools.gen_pet_assets import FRAMES, render_frame
from tools.import_pet_images import _bbox, frame_diffs, measure_foot_line

#: 相邻帧差异上限：超过就是"轮廓每帧换位置"级别的抖动
MAX_FRAME_DIFF = 0.08
#: 相邻帧不许像素级重复（重复等于把 6 帧播成 3 帧）
MIN_DISTINCT_DIFF = 0.0005
#: 一圈下来必须看得见动静，否则就是静图
MIN_CYCLE_MOTION = 0.012


def _digest(image: QImage) -> bytes:
    """帧内容指纹。

    不能用 `image.constBits()`：那块内存不归 QImage 对象拥有，取完就可能被释放，
    在 pytest 里实测读到过混着堆地址的脏数据（六帧只算出两种“内容”），会
    把正常素材报成重复帧。编码成 PNG 字节才是确定性的。
    """
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def _block(x: int, size: int = 64, color: str = "#333333") -> QImage:
    """在指定横坐标画一块实心方块，用来构造"已知差异量"的帧。"""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRect(x, 10, 20, 40)
    p.end()
    return img


def _flat(alpha: int, size: int = 64) -> QImage:
    """整幅单一透明度的图：用来构造“已知差异量”的帧。"""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor(40, 40, 40, alpha))
    return img


@pytest.fixture(scope="module")
def frames_by_action(qapp):
    """十个动作各渲染一次：四条断言共用，避免重复画 180 帧。"""
    return {action: [render_frame(action, i) for i in range(FRAMES)]
            for action in ACTION_NAMES}


@pytest.mark.parametrize("action", sorted(ACTION_NAMES))
def should_have_no_repeated_consecutive_frames(frames_by_action, action):
    assert min(frame_diffs(frames_by_action[action])) > MIN_DISTINCT_DIFF, action


@pytest.mark.parametrize("action", sorted(ACTION_NAMES))
def should_render_six_pairwise_distinct_frames(frames_by_action, action):
    """只比相邻两帧不够：对称波形恰好是"隔三帧重复"，相邻全不一样但六帧只有三帧。"""
    images = frames_by_action[action]
    assert len(images) == FRAMES, action
    # QImage.__eq__ 比的是底层数据共享（对象同一性），两个独立渲染出来的
    # 一模一样的帧在这里也不相等——这条闸必须比内容，否则形同没写
    blobs = [_digest(image) for image in images]
    same = [(i + 1, j + 1) for i in range(FRAMES) for j in range(i + 1, FRAMES)
            if blobs[i] == blobs[j]]
    assert not same, f"{action} 六帧里有相同帧：{same}"


@pytest.mark.parametrize("action", sorted(ACTION_NAMES))
def should_keep_silhouette_stable_across_frames(frames_by_action, action):
    """动的是姿态，不是轮廓毛刺：任何一步都不许改到 8% 的剪影。"""
    assert max(frame_diffs(frames_by_action[action])) < MAX_FRAME_DIFF, action


@pytest.mark.parametrize("action", sorted(ACTION_NAMES))
def should_show_visible_motion_each_cycle(frames_by_action, action):
    assert sum(frame_diffs(frames_by_action[action])) / FRAMES >= MIN_CYCLE_MOTION, action


@pytest.mark.parametrize("action", sorted(ACTION_NAMES))
def should_have_six_distinct_frames_on_disk(action):
    """上面几条测的是生成器当场渲染的帧，这一条测真机实际读的磁盘文件。

    两者会分家：生成器修好了波形，但仓里还躺着旧版生成的 PNG，测试全绿而真机
    仍在播重复帧。这里直接拿落盘素材比字节，素材过旧当场就红。
    """
    root = Path(__file__).resolve().parents[2] / "assets" / "pet"
    dirs = sorted(p for p in root.iterdir() if (p / action).is_dir())
    if not dirs:
        pytest.skip("仓内无素材")
    for skin_dir in dirs:
        files = sorted((skin_dir / action).glob("*.png"))
        assert len(files) == FRAMES, f"{skin_dir.name}/{action} 只有 {len(files)} 帧"
        digests = {p.read_bytes() for p in files}      # 文件字节：最硬的一致性判据
        assert len(digests) == FRAMES, \
            f"{skin_dir.name}/{action} 落盘帧有重复，素材比代码旧：{[p.name for p in files]}"


class TestFrameDiffMetric:
    """指标错了比素材错了更危险：它会指挥人去做不存在的修复。"""

    def should_report_zero_for_identical_images(self, qapp):
        images = [_block(10) for _ in range(FRAMES)]
        assert frame_diffs(images) == [0.0] * FRAMES

    def should_report_shift_between_two_known_frames(self, qapp):
        """方块从 x=10 移到 x=30：两帧时末→首回环与 1→2 是同一个变化量。"""
        diffs = frame_diffs([_block(10), _block(30)])
        assert diffs[0] == diffs[1] > 0, diffs
        # 只动了局部：绝不该出现“整幅画面都在变”那种量级，错位 bug 正是这样
        assert diffs[0] < 0.5, diffs

    def should_report_full_change_when_nothing_overlaps(self, qapp):
        """满帧透明与满帧不透明轮流：相邻差异必须是 100%，不能漏报。"""
        frames = [_flat(255 if i % 2 else 0) for i in range(FRAMES)]
        diffs = frame_diffs(frames)
        assert all(d > 0.99 for d in diffs), [round(d, 3) for d in diffs]

    def should_compare_frames_not_pixels_within_one_frame(self, qapp):
        """错位写法会让“六张完全相同的图”报出非零差异——这条正面钉死它。"""
        same = [_block(10) for _ in range(FRAMES)]
        shifted = [_block(11) for _ in range(FRAMES)]
        assert set(frame_diffs(same)) == {0.0}
        # 回环差只可能出现在“末帧→首帧”那一步，其余五步必须全为 0
        assert set(frame_diffs(same + shifted)[:FRAMES - 1]) == {0.0}


def should_align_imported_assets_with_installed_foot_line(qapp):
    """两套素材各锚各的脚线会看到上下跳：导入器必须去对齐已装帧的脚线。

    生成器画出的脚比标称 BASE_Y 低一截（实测 224 vs 216），这个偏差本身无所谓，
    致命的是两边不一致。这里拿仓里真实素材做参考，所以生成器改了脚位也能跟上。
    """
    root = Path(__file__).resolve().parents[2] / "assets"
    ref = root / "pet" / "dog" / "idle" / "01.png"
    if not ref.exists():
        pytest.skip("仓内素材不在，无法对齐脚线")
    foot = measure_foot_line(root, "cat")
    assert foot == _bbox(QImage(str(ref)))[3]
    assert abs(foot - 216.0) <= 12.0        # 漂移过大就是有人改了地平线，要同步常量
