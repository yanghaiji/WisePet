"""把外部（AI 生成）的贴纸图导入成桌宠序列帧素材（PRD v2.0 C9 的素材接入口）。

为什么要走这个脚本而不是直接把图丢进 assets/：AI 每帧的角色大小、站位、底色都不完全一样，
loader 只按文件名排序轮播、不做任何对齐——直接丢进去的结果是"抖动 + 带一块白底"。
本脚本把每帧统一成：抠掉背景 → 按内容包围盒等比缩放 → 脚底贴到同一条地平线 →
水平居中 → 256×256 RGBA PNG（画布参数与 tools/gen_pet_assets.py 完全一致）。

用法：
    # 推荐：一个动作一张网格图（一个皮肤 10 张图，帧序从左到右、从上到下）
    python tools/import_pet_images.py --in tmp/ai/cat --skin cat --preview

    # 或者：一帧一张图，放在以动作命名的子目录里（idle/01.png … idle/05.png）
    python tools/import_pet_images.py --in tmp/ai/cat --skin cat --layout single

可选：
    --bg auto|keep|cut   auto=原图已有透明通道就保留、否则抠图；keep=不抠；cut=强制抠
    --keep-extra         保留角色以外的独立小块（食盆/投影/水印）；默认只留最大连通块
    --tol 40             抠图色差容差（越大抠得越狠，边缘越薄）
    --dry-run            只检查命名与内容，不写文件
    --preview            额外输出 tmp/imported-{skin}.png 总览图供目检

素材落盘契约（tests/integration/test_pet_assets.py 会校验）：
    assets/pet/{skin}/{action}/01.png … 09.png，256×256、RGBA、透明背景、每动作正好 10 帧。
"""
from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402

from WisePet.domain.actions import (  # noqa: E402
    ACTION_NAMES,
    FRAMES_PER_ACTION,
    FRAME_FOOT_Y,
    FRAME_SOURCE_SIZE,
)

SIZE = FRAME_SOURCE_SIZE        # 画布边长：与生成器、运行时同源于 domain.actions 素材契约
CX = SIZE / 2.0                 # 水平中心
BASE_Y = float(FRAME_FOOT_Y)    # 脚底地平线兜底值：实际以已装素材量出来的为准（见 measure_foot_line）
TARGET_H = 186.0              # 角色内容高度（生成器约 30→216）
TARGET_W = 210.0              # 内容宽度上限：伸懒腰这类横躺姿态不许溢出画布
ALPHA_EDGE = 8                # 判定"有内容"的最小 alpha
WORK_MAX = 256                # 抠图前先缩到该尺寸：纯 Python 泛洪才跑得动
# FRAMES_PER_ACTION 不在此重写：素材帧数的单一真相源在 domain.actions
HOLE_SEAL_RADIUS = 0          # 填洞前把不透明区膨胀几像素封腿缝：0=关掉填洞。
#                             实测关掉：肚皮是白色，透明肚皮透出白桌面＝和填白一样看不出，
#                             而膨胀填洞会把邻格残片与角色连成一块、撑大包围盒把猫裁掉，得不偿失。


def _scaled(img: QImage, limit: int) -> QImage:
    """等比缩到长边不超过 limit。"""
    if max(img.width(), img.height()) <= limit:
        return img
    ratio = limit / max(img.width(), img.height())
    return img.scaled(max(1, round(img.width() * ratio)),
                      max(1, round(img.height() * ratio)),
                      Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def _has_alpha(img: QImage) -> bool:
    """是否存在半透明/全透明像素（用来判断"这张图已经抠好了"）。"""
    if not img.hasAlphaChannel():
        return False
    work = _scaled(img, WORK_MAX).convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = work.width(), work.height()
    buf = work.bits()                     # 本机 PySide6：bits() 直接是可写 memoryview
    return any(buf[i] < 250 for i in range(3, w * h * 4, 4))


def _cut_background(img: QImage, tol: int) -> QImage:
    """从四边泛洪抠底：只吃与边框连通的近底色像素，不吃角色身上的同色块。

    种子色取四角均值——贴纸风素材的底色几乎总是纯色，取角比取整图众数便宜得多。
    不做预缩放：泛洪 O(w*h)，哪怕 256×1152=29 万像素也毫秒级跑完，
    缩了反而破坏宽高比把猫压扁（见 WORK_MAX 历史坑）。
    """
    work = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = work.width(), work.height()
    buf = work.bits()

    corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    seed = [sum(buf[((y * w) + x) * 4 + c] for x, y in corners) // 4 for c in range(3)]

    def near(idx: int) -> bool:
        i = idx * 4
        return all(abs(buf[i + c] - seed[c]) <= tol for c in range(3))

    seen = bytearray(w * h)
    queue: deque[int] = deque()
    for x in range(w):                       # 上下一条边
        for y in (0, h - 1):
            idx = y * w + x
            if not seen[idx] and near(idx):
                seen[idx] = 1
                queue.append(idx)
    for y in range(h):                       # 左右一条边
        for x in (0, w - 1):
            idx = y * w + x
            if not seen[idx] and near(idx):
                seen[idx] = 1
                queue.append(idx)

    while queue:                             # 4 邻域泛洪
        idx = queue.popleft()
        buf[idx * 4 + 3] = 0
        x, y = idx % w, idx // w
        for nidx in (idx - 1 if x else -1, idx + 1 if x < w - 1 else -1,
                     idx - w if y else -1, idx + w if y < h - 1 else -1):
            if 0 <= nidx < w * h and not seen[nidx] and near(nidx):
                seen[nidx] = 1
                queue.append(nidx)
    _fill_enclosed_holes(work, HOLE_SEAL_RADIUS)
    return work


def _fill_enclosed_holes(work: QImage, radius: int) -> None:
    """把被角色包住的透明洞原地补回不透明（泛洪只清了 alpha，RGB 还在，颜色直接复现）。

    AI 贴纸的白肚皮和纯白背景同色，_cut_background 的泛洪会顺着腿间细缝爬进身体、
    把肚皮一起抠成透明——真机上表现为肚皮透出桌面（后面是白窗口就"卡白"）。实测这些
    洞 99.8% 与边缘连通，直接填封闭洞无效；所以先把不透明区膨胀 radius 像素封住腿缝，
    再从边缘泛洪找真·外部背景，凡是原本透明又够不到边缘的就是被误抠的内部洞，补回。
    """
    if radius <= 0:
        return
    w, h = work.width(), work.height()
    buf = work.bits()                          # 绑定住：memoryview 不拥有内存
    total = w * h
    opaque = bytearray(1 if buf[i * 4 + 3] > ALPHA_EDGE else 0 for i in range(total))

    dilated = bytearray(total)                # 盒膨胀：radius 邻域内有不透明即算不透明
    for y in range(h):
        if not any(opaque[y * w: y * w + w]):
            continue
        y0, y1 = max(0, y - radius), min(h, y + radius + 1)
        for x in range(w):
            if not opaque[y * w + x]:
                continue
            x0, x1 = max(0, x - radius), min(w, x + radius + 1)
            for yy in range(y0, y1):
                row = yy * w
                for xx in range(x0, x1):
                    dilated[row + xx] = 1

    outer = bytearray(total)                  # 从边缘泛洪"膨胀后的背景"= 真外部
    queue: deque[int] = deque()
    for x in range(w):
        for y in (0, h - 1):
            i = y * w + x
            if not dilated[i] and not outer[i]:
                outer[i] = 1
                queue.append(i)
    for y in range(h):
        for x in (0, w - 1):
            i = y * w + x
            if not dilated[i] and not outer[i]:
                outer[i] = 1
                queue.append(i)
    while queue:
        i = queue.popleft()
        x = i % w
        for n in (i - 1 if x else -1, i + 1 if x < w - 1 else -1,
                  i - w if i >= w else -1, i + w if i < total - w else -1):
            if 0 <= n < total and not dilated[n] and not outer[n]:
                outer[n] = 1
                queue.append(n)

    for i in range(total):                    # 补洞：原透明且够不到边缘 → 内部误抠洞
        if opaque[i] or outer[i]:
            continue
        x, y = i % w, i // w
        # 离图边太近的透明区不填：膨胀会把"角色与画布边缘之间的细缝"也封成假洞，
        # 填了会多出一条白条、把包围盒撑大导致角色被裁——肚皮在中央，不受这个保护影响。
        if x < radius or x >= w - radius or y < radius or y >= h - radius:
            continue
        buf[i * 4 + 3] = 255


def _keep_main_component(img: QImage) -> QImage:
    """只保留最大连通块：水印文字、脚底投影、尾巴速度线都是独立小块，一并吃掉。

    不这么做的话，AI 图上的“豆包 AI 生成”会被当成内容，包围盒被拉到右下角，
    角色缩成一小只且逐帧大小不一。副作用：胡须这类细线也会跟着被丢掉——
    六帧一致地没有，比有的帧有、一帧没有好。
    """
    work = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = work.width(), work.height()
    buf = work.bits()
    total = w * h

    def neighbours(idx: int) -> tuple[int, ...]:
        x = idx % w
        return (idx - 1 if x else -1, idx + 1 if x < w - 1 else -1,
                idx - w if idx >= w else -1, idx + w if idx < total - w else -1)

    def opaque(idx: int) -> bool:
        return buf[idx * 4 + 3] > ALPHA_EDGE

    seen = bytearray(total)
    biggest: list[int] = []
    for start in range(total):
        if seen[start] or not opaque(start):
            continue
        seen[start] = 1
        queue = deque([start])
        comp = []
        while queue:
            idx = queue.popleft()
            comp.append(idx)
            for nidx in neighbours(idx):
                if 0 <= nidx < total and not seen[nidx] and opaque(nidx):
                    seen[nidx] = 1
                    queue.append(nidx)
        if len(comp) > len(biggest):
            biggest = comp

    keep = bytearray(total)
    for idx in biggest:
        keep[idx] = 1
    for idx in range(total):
        if opaque(idx) and not keep[idx]:
            buf[idx * 4 + 3] = 0
    return work


def _bbox(img: QImage) -> tuple[int, int, int, int] | None:
    """内容包围盒 (x0, y0, x1, y1)；整张透明返回 None。"""
    w, h = img.width(), img.height()
    work = img.convertToFormat(QImage.Format.Format_RGBA8888)   # 绑定住：memoryview 不拥有内存
    buf = work.bits()
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = y * w
        for x in range(w):
            if buf[(row + x) * 4 + 3] > ALPHA_EDGE:
                x0 = x if x < x0 else x0
                x1 = x if x > x1 else x1
                y0 = y if y < y0 else y0
                y1 = y if y > y1 else y1
    if x1 < 0:
        return None
    return x0, y0, x1, y1


def _strip_edge_halo(img: QImage, threshold: int = 20) -> QImage:
    """抠背景+主连通块之后清半透明边缘残留。

    AI 网格图的背景和角色之间通常有一层抗锯齿过渡像素（alpha 30-200），
    泛洪只吃 alpha=0 和近底色的，这层过渡就留在了落盘帧边缘——
    真机上表现为"左侧有黑线/白边"或"脚底有灰雾"。
    主连通块之后再扫一遍，把 alpha < threshold 的全清零，
    角色主体（alpha > 200）不受影响。
    """
    work = img.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = work.bits()
    total = work.width() * work.height()
    for i in range(total):
        if 0 < buf[i * 4 + 3] < threshold:
            buf[i * 4 + 3] = 0
    return work


def _shrink_bbox_edges(img: QImage, box: tuple[int, int, int, int], max_shrink: int = 8) -> tuple[int, int, int, int]:
    """bbox 各边向内收缩，切掉抠背景/切分带来的边缘残留。

    两类残留：
    1. "隔壁帧黑色描边"：等分切分切到相邻帧的黑色轮廓，带进来 1-5px 宽的窄条。
       检测：整列/行暗色（亮度 < 60）比例 > 70%。
    2. "背景抗锯齿残留"：白色背景抠掉后留下一整列浅灰色 (亮度 180-240)、
       alpha 还挺高 (100-200) 的边缘。在透明窗口上看起来像黑线/灰线。
       检测：整列/行颜色高度一致（方差 < 50），并且既不是猫色也不是暗色。
    """
    x0, y0, x1, y1 = box
    if x1 - x0 < 20 or y1 - y0 < 20:
        return box

    rgba = img.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = rgba.bits()
    w, h = rgba.width(), rgba.height()

    def col_color_profile(x: int) -> tuple[float, float, float, int]:
        """返回 (暗色占比, 亮色纯色占比, 方差, 总非透明像素数)。"""
        dark = light_solid = total = 0
        total_r = total_g = total_b = 0
        sum_sq = 0.0
        for yy in range(y0, y1 + 1):
            idx = (yy * w + x) * 4
            a = buf[idx + 3]
            if a < 20:
                continue
            total += 1
            r, g, b = buf[idx], buf[idx + 1], buf[idx + 2]
            bright = (r + g + b) // 3
            if bright < 60:
                dark += 1
            elif bright > 180:
                light_solid += 1
            total_r += r
            total_g += g
            total_b += b
        if total == 0:
            return 0.0, 0.0, 0.0, 0
        avg_r = total_r / total
        avg_g = total_g / total
        avg_b = total_b / total
        for yy in range(y0, y1 + 1):
            idx = (yy * w + x) * 4
            if buf[idx + 3] < 20:
                continue
            dr = buf[idx] - avg_r
            dg = buf[idx + 1] - avg_g
            db = buf[idx + 2] - avg_b
            sum_sq += dr * dr + dg * dg + db * db
        variance = sum_sq / total
        return dark / total, light_solid / total, variance, total

    def row_color_profile(y: int) -> tuple[float, float, float, int]:
        dark = light_solid = total = 0
        sum_sq = 0.0
        total_r = total_g = total_b = 0
        for xx in range(x0, x1 + 1):
            idx = (y * w + xx) * 4
            a = buf[idx + 3]
            if a < 20:
                continue
            total += 1
            r, g, b = buf[idx], buf[idx + 1], buf[idx + 2]
            bright = (r + g + b) // 3
            if bright < 60:
                dark += 1
            elif bright > 180:
                light_solid += 1
            total_r += r
            total_g += g
            total_b += b
        if total == 0:
            return 0.0, 0.0, 0.0, 0
        avg_r = total_r / total
        avg_g = total_g / total
        avg_b = total_b / total
        for xx in range(x0, x1 + 1):
            idx = (y * w + xx) * 4
            if buf[idx + 3] < 20:
                continue
            dr = buf[idx] - avg_r
            dg = buf[idx + 1] - avg_g
            db = buf[idx + 2] - avg_b
            sum_sq += dr * dr + dg * dg + db * db
        variance = sum_sq / total
        return dark / total, light_solid / total, variance, total

    def is_edge_residue(dark_ratio: float, light_ratio: float, variance: float, total: int) -> bool:
        if total < 15:
            return True
        if dark_ratio > 0.7:
            return True
        if light_ratio > 0.95:
            return True
        if light_ratio > 0.85 and variance < 200:
            return True
        return False

    while x0 < x1 and x0 + max_shrink <= x1:
        d, l, v, t = col_color_profile(x0)
        if is_edge_residue(d, l, v, t):
            x0 += 1
        else:
            break
    while x1 > x0 and x1 - max_shrink >= x0:
        d, l, v, t = col_color_profile(x1)
        if is_edge_residue(d, l, v, t):
            x1 -= 1
        else:
            break
    while y0 < y1 and y0 + max_shrink <= y1:
        d, l, v, t = row_color_profile(y0)
        if is_edge_residue(d, l, v, t):
            y0 += 1
        else:
            break
    while y1 > y0 and y1 - max_shrink >= y0:
        d, l, v, t = row_color_profile(y1)
        if is_edge_residue(d, l, v, t):
            y1 -= 1
        else:
            break

    return x0, y0, x1, y1


def _normalize(img: QImage, name: str, foot_line: float = BASE_Y) -> QImage:
    """按内容包围盒归一到统一画布：脚底贴地平线、水平居中、等比缩放到固定身高。"""
    box = _bbox(img)
    if box is None:
        raise ValueError(f"{name}：整张图都是透明的，检查抠图容差 --tol 或原图内容")
    box = _shrink_bbox_edges(img, box)
    x0, y0, x1, y1 = box
    content = img.copy(x0, y0, x1 - x0 + 1, y1 - y0 + 1)
    cw, ch = content.width(), content.height()
    scale = min(TARGET_H / ch, TARGET_W / cw)
    tw, th = max(1, round(cw * scale)), max(1, round(ch * scale))
    placed = content.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)

    out = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage(round(CX - tw / 2), round(foot_line - th), placed)   # 底边贴地平线
    p.end()

    out_rgba = out.convertToFormat(QImage.Format.Format_RGBA8888)
    obuf = out_rgba.bits()
    ow, oh = SIZE, SIZE
    ob = _bbox(out_rgba)
    if ob:
        ox0, oy0, ox1, oy1 = ob
        pad = 3
        for y in range(max(0, oy0 - pad), min(oh, oy1 + pad + 1)):
            for x in range(max(0, ox0 - pad), min(ow, ox1 + pad + 1)):
                near_edge = (
                    x <= ox0 + pad or x >= ox1 - pad or
                    y <= oy0 + pad or y >= oy1 - pad
                )
                if not near_edge:
                    continue
                idx = (y * ow + x) * 4
                a = obuf[idx + 3]
                if a < 20:
                    continue
                r, g, b = obuf[idx], obuf[idx + 1], obuf[idx + 2]
                if abs(r - g) <= 4 and abs(g - b) <= 4 and abs(r - b) <= 4:
                    bright = (r + g + b) // 3
                    if 60 <= bright <= 210:
                        obuf[idx + 3] = 0
                elif a < 200:
                    obuf[idx + 3] = 0
    return out_rgba


def measure_foot_line(assets_root: Path, skin: str) -> float:
    """量“还留着程序绘制帧”的皮肤基准，让 AI 图去对齐它。

    两套素材各锚各的脚线会差 9px（生成器画的脚比标称地平线低一截），混用时
    idle↔walk 切换会看得到上下跳。拿另一个皮肤的 idle 作参考：它不会被本次导入
    碰到；全部皮肤都换成 AI 图后，参考就变成 AI 自己的脚线，仍然自洽。
    """
    for ref_skin in ("dog", "monkey", "cat"):
        if ref_skin == skin:
            continue
        probe = assets_root / "pet" / ref_skin / "idle" / "01.png"
        if probe.exists():
            box = _bbox(QImage(str(probe)))
            if box:
                return float(box[3])
    return BASE_Y


def _col_nonwhite_counts(sheet: QImage) -> list[int]:
    W, H = sheet.width(), sheet.height()
    rgba = sheet.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = rgba.bits()
    col = [0] * W
    for y in range(H):
        row = y * W
        for x in range(W):
            i = (row + x) * 4
            if not (buf[i] > 240 and buf[i + 1] > 240 and buf[i + 2] > 240):
                col[x] += 1
    return col


def _equal_segments(W: int, count: int) -> list[tuple[int, int]]:
    xs = [round(W * c / count) for c in range(count + 1)]
    return [(xs[i], xs[i + 1] - 1) for i in range(count)]


def _detect_segments(sheet: QImage, target_count: int) -> list[tuple[int, int]]:
    """找 target_count 个内容段，每段 = 一只完整小猫的 x 范围。

    策略：直接找**间隙**（smoothed < 阈值的列区间），间隙应该有 target_count-1 个。
    间隙之间就是小猫。AI 网格里小猫会把尾巴/耳朵伸到间隙里造"小孤岛"，
    所以间隙合并时会忽略 < 20px 的内容孤岛，把它们并回相邻大段。
    """
    W, H = sheet.width(), sheet.height()
    if W < 100:
        return _equal_segments(W, target_count)

    rgba = sheet.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = rgba.bits()
    col_counts = [0] * W
    for y in range(H):
        row = y * W
        for x in range(W):
            i = (row + x) * 4
            if not (buf[i] > 240 and buf[i + 1] > 240 and buf[i + 2] > 240):
                col_counts[x] += 1

    smoothed = []
    for i in range(W):
        lo, hi = max(0, i - 3), min(W, i + 4)
        smoothed.append(sum(col_counts[lo:hi]) / (hi - lo))

    GAP_THRESH = 12
    gaps, in_gap, gap_start = [], False, 0
    for x in range(W):
        if smoothed[x] < GAP_THRESH:
            if not in_gap:
                gap_start, in_gap = x, True
        elif in_gap:
            gaps.append((gap_start, x - 1))
            in_gap = False
    if in_gap:
        gaps.append((gap_start, W - 1))

    if len(gaps) == 0:
        return _equal_segments(W, target_count)

    content = []
    prev_end = -1
    for gs, ge in gaps:
        if prev_end >= 0 and gs - prev_end - 1 > 0:
            content.append((prev_end + 1, gs - 1))
        prev_end = ge
    if prev_end < W - 1:
        content.append((prev_end + 1, W - 1))

    MIN_CONTENT = 20
    content = [(s, e) for s, e in content if e - s + 1 >= MIN_CONTENT]

    if len(content) == 0:
        return _equal_segments(W, target_count)

    if len(content) == target_count:
        return content

    if len(content) < target_count:
        content = _split_content(content, smoothed, target_count)

    if len(content) > target_count:
        content = _merge_content(content, target_count)

    if len(content) == target_count:
        return content

    return _equal_segments(W, target_count)


def _split_content(content, smoothed, target_count):
    while len(content) < target_count:
        widest = max(content, key=lambda s_e: s_e[1] - s_e[0])
        s, e = widest
        w_ = e - s + 1
        target_w = (smoothed.__len__() and 1) or 1
        avg_w = sum(e2 - s2 + 1 for s2, e2 in content) / len(content)
        if w_ < avg_w * 1.3:
            break
        mid = (s + e) // 2
        best_x, best_val = -1, float('inf')
        search_lo = max(s + 20, mid - w_ // 4)
        search_hi = min(e - 20, mid + w_ // 4)
        for x in range(search_lo, search_hi + 1):
            if smoothed[x] < best_val:
                best_val, best_x = smoothed[x], x
        if best_x < 0 or best_val > 30:
            break
        content.remove(widest)
        content.append((s, best_x - 1))
        content.append((best_x, e))
    content.sort()
    return content


def _merge_content(content, target_count):
    while len(content) > target_count:
        best_pair, best_gap = None, float('inf')
        for i in range(len(content) - 1):
            _, e1 = content[i]
            s2, _ = content[i + 1]
            g = s2 - e1 - 1
            if g < best_gap:
                best_gap, best_pair = g, i
        if best_pair is None:
            break
        i = best_pair
        s1, _ = content[i]
        _, e2 = content[i + 1]
        content[i] = (s1, e2)
        del content[i + 1]
    return content


def _cells(sheet: QImage, rows: int, cols: int) -> list[QImage]:
    """网格图切成 rows×cols 个单元，按从左到右、从上到下读作帧 1..N。

    先扫一遍等分点附近是不是空白（±5px 内非白像素 < GAP_THRESH）：
    - 如果等分点正好是间隙（docs/images 系列），直接等分切
    - 如果等分点切到了内容（cat-ref 系列），用水平投影智能切
    多行网格暂不做行方向智能检测——AI 基本只出单行 8 列。
    """
    if rows == 1:
        W = sheet.width()
        eq = _equal_segments(W, cols)

        col_counts = _col_nonwhite_counts(sheet)
        GAP_CHECK = 12
        blank_at = sum(
            1 for cut in eq[1:-1]
            if all(col_counts[x] < GAP_CHECK for x in range(max(0, cut[0] - 5), min(W, cut[0] + 6)))
        )
        perfect = blank_at >= len(eq) - 2

        if perfect:
            return [sheet.copy(s, 0, e - s + 1, sheet.height()) for s, e in eq]

        xs = _detect_segments(sheet, cols)
        return [sheet.copy(max(0, xs[c][0] - 5), 0,
                           min(xs[c][1] + 6, sheet.width()) - max(0, xs[c][0] - 5),
                           sheet.height())
                for c in range(cols)]

    W, H = sheet.width(), sheet.height()
    xs = [round(W * c / cols) for c in range(cols + 1)]
    ys = [round(H * r / rows) for r in range(rows + 1)]
    return [sheet.copy(xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r])
            for r in range(rows) for c in range(cols)]


def _match_action(stem: str) -> str | None:
    """文件名里认动作名：允许 cat_idle、idle、idle-6frame 之类的写法。"""
    low = stem.lower()
    for action in ACTION_NAMES:
        if action in low:
            return action
    return None


def _collect(src_dir: Path, layout: str) -> dict[str, list[Path]]:
    """按布局归集输入：grid=一动作一张图；single=一动作一个目录。

    grid 递归扫子目录：图常按 idle/ walk/ run/ 分文件夹放。只拿文件名认动作，
    目录名与盘符路径不参与，免得路径里偶然出现某个动作名时全部误归到它名下。
    """
    if layout == "grid":
        groups: dict[str, list[Path]] = {}
        for path in sorted(src_dir.rglob("*.png")):
            action = _match_action(path.stem)
            if action is None:
                continue
            groups.setdefault(action, []).append(path)
        return groups

    groups = {}
    for action_dir in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        frames = sorted(p for p in action_dir.iterdir()
                        if p.is_file() and p.suffix.lower() == ".png")
        if action_dir.name in ACTION_NAMES and frames:
            groups[action_dir.name] = frames
    return groups


def _pick(cells: list[QImage], want: int) -> list[QImage]:
    """从网格/目录里抽 `want` 帧：多于就等间隔采样，少于就报错。

    AI 常出 2×5=10 格，而程序契约是每动作 10 帧。等间隔取（`i*len/want` 向下取整）
    而不是截前六张：截断会把循环的尾段丢掉，播起来最后一帧硬跳回第一帧。
    """
    if len(cells) < want:
        raise ValueError(f"只有 {len(cells)} 帧，每动作需要 {want} 帧"
                         f"（网格图请用 --rows/--cols 对准格数）")
    if len(cells) == want:
        return list(cells)
    return [cells[i * len(cells) // want] for i in range(want)]


def import_skin(src_dir: Path, skin: str, layout: str, rows: int, cols: int,
                bg_mode: str, tol: int, keep_extra: bool = False,
                want_frames: int = FRAMES_PER_ACTION,
                foot_line: float = BASE_Y,
                only: set[str] | None = None) -> dict[str, list[QImage]]:
    """读入并按契约归一，返回 {action: [want_frames 帧 QImage]}；不合规直接报错，不静默凑数。"""
    groups = _collect(src_dir, layout)
    for action, paths in groups.items():
        if layout == "grid" and len(paths) > 1:
            raise ValueError(f"{action}：grid 模式一个动作只能一张图，收到 "
                             f"{', '.join(p.name for p in paths)}")

    result: dict[str, list[QImage]] = {}
    for action in ACTION_NAMES:
        paths = groups.get(action, [])
        if not paths or (only is not None and action not in only):
            continue
        if layout == "grid":
            sheet = QImage(str(paths[0]))
            if sheet.isNull():
                raise ValueError(f"{paths[0]} 读不出来，不是有效 PNG")
            cells = _cells(sheet, rows, cols)
        else:
            cells = [QImage(str(p)) for p in paths]
            if any(c.isNull() for c in cells):
                raise ValueError(f"{action}：有文件不是有效 PNG")

        chosen = _pick(cells, want_frames)
        frames = []
        for i, cell in enumerate(chosen, start=1):
            keep = bg_mode == "keep" or (bg_mode == "auto" and _has_alpha(cell))
            work = cell if keep else _cut_background(cell, tol)
            if not keep_extra:
                work = _keep_main_component(work)
                work = _strip_edge_halo(work)
            frames.append(_normalize(work, f"{skin}/{action}/帧{i}", foot_line))
        result[action] = frames
    return result


#: 帧来源标记文件名：gen_pet_assets.py 靠它识别哪些动作是外部导入的
PROVENANCE_FILE = ".imported"
#: 帧间差异体检的粗粒度：够读出“六帧是不是同一张”，又不为几个像素的抗锯齿纠结
MASK_PX = 64
#: 相邻帧平均差异阈值：低于此值播起来就是“一张静图在抽”，AI 常犯
MIN_FRAME_DIFF = 0.012


def _mask(img: QImage) -> list[int]:
    small = img.scaled(MASK_PX, MASK_PX, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.FastTransformation)
    work = small.convertToFormat(QImage.Format.Format_RGBA8888)   # 绑住：bits() 不拥有内存
    buf = work.bits()
    return [1 if buf[i * 4 + 3] > ALPHA_EDGE else 0 for i in range(MASK_PX * MASK_PX)]


def frame_diffs(images: list[QImage]) -> list[float]:
    """相邻帧（含末→首回环）的差异比例：变化像素数 / 总像素数。"""
    masks = [_mask(image) for image in images]
    # 逐位比两张等长 mask 必须 zip(a, b)：写成 zip(a, b[i+1:]) 比的是 a 自己错位
    # （下标取模用帧数 6、而序列长 4096，差异全落在 a[4090:4096] 那 6 个元素上），
    # 量出来的数与画面无关——曾据此把正常素材误判成“浑身发抖”。
    return [sum(1 for a, b in zip(masks[i], masks[(i + 1) % len(masks)]) if a != b)
            / (MASK_PX * MASK_PX) for i in range(len(masks))]


def report_motion(frames: dict[str, list[QImage]]) -> list[str]:
    """把“几乎不动”的动作报出来：画风目检是主观的，帧间差异是数字。"""
    quiet = []
    for action, images in frames.items():
        mean = sum(frame_diffs(images)) / len(images)
        if mean < MIN_FRAME_DIFF:
            quiet.append(f"{action} 帧间差异 {mean * 100:.1f}%（建议 ≥ "
                         f"{MIN_FRAME_DIFF * 100:.1f}%，否则播起来像静图）")
    return quiet


def write_frames(out_root: Path, skin: str, frames: dict[str, list[QImage]],
                 source: str = "") -> int:
    """落盘并留下 `.imported` 来源标记。

    标记是给 gen_pet_assets.py 看的：它重生整个皮肤时会读这个文件，发现动作是外部
    图就停下，不把人家用钱换来的图静默盖掉。光看 PNG 分不出来源（Qt 把两种图都
    归一成 ARGB32），所以必须显式记。不匹配已知后缀的文件不会进目录，不会误伤。
    """
    total = 0
    for action, images in frames.items():
        directory = out_root / "pet" / skin / action
        directory.mkdir(parents=True, exist_ok=True)
        for i, image in enumerate(images, start=1):
            target = directory / f"{i:02d}.png"
            if not image.save(str(target), "PNG"):
                raise OSError(f"素材写入失败：{target}")
            total += 1
        (directory / PROVENANCE_FILE).write_text(
            source or "unknown", encoding="utf-8", errors="replace")
    return total


def write_preview(path: Path, frames: dict[str, list[QImage]]) -> Path:
    """拼一张总览图（白底，只为目检；落盘素材是透明底）。

    行序跟 ACTION_NAMES 而不是字母序：与 gen_pet_assets.py 的预览图同构，
    两张图放一起比行次才不会对不上。
    """
    actions = [a for a in ACTION_NAMES if a in frames]
    if not actions:
        return None                     # 零帧拼不出合法 QImage（宽 0），QPainter 会退化到打印机引擎
    cols = max(len(v) for v in frames.values()) if frames else FRAMES_PER_ACTION
    sheet = QImage(SIZE * cols, SIZE * len(actions), QImage.Format.Format_ARGB32)
    sheet.fill(Qt.GlobalColor.white)
    p = QPainter(sheet)
    for row, action in enumerate(actions):
        for col, image in enumerate(frames[action]):
            p.drawImage(col * SIZE, row * SIZE, image)
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(path), "PNG"):
        raise OSError(f"预览图写入失败：{path}")
    return path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="导入外部 AI 生图为桌宠序列帧素材")
    parser.add_argument("--in", dest="src", required=True, help="外部图片所在目录")
    parser.add_argument("--skin", required=True, help="皮肤 id：cat | dog | monkey")
    parser.add_argument("--layout", default="grid", choices=("grid", "single"),
                        help="grid=一动作一张网格图（默认）；single=一帧一张图")
    parser.add_argument("--rows", type=int, default=2, help="grid 模式行数")
    parser.add_argument("--cols", type=int, default=5, help="grid 模式列数")
    parser.add_argument("--frames", type=int, default=FRAMES_PER_ACTION,
                        help=f"每个动作最终落盘几帧（程序契约 {FRAMES_PER_ACTION}）；"
                             f"格数多于它会等间隔采样，少于它报错")
    parser.add_argument("--out", default=str(root / "assets"), help="素材根目录")
    parser.add_argument("--bg", default="auto", choices=("auto", "keep", "cut"),
                        help="抠背景方式")
    parser.add_argument("--tol", type=int, default=40, help="抠图色差容差")
    parser.add_argument("--keep-extra", action="store_true",
                        help="保留角色之外的独立小块（食盆/投影/水印）；默认只留最大连通块")
    parser.add_argument("--foot-line", type=float, default=None,
                        help="脚底地平线：默认从已装素材量出来（保证 AI 图与程序素材齐平）")
    parser.add_argument("--only", default="",
                        help="只导入这些动作（逗号分隔）：图放齐了但不想把某个动作换掉时用")
    parser.add_argument("--dry-run", action="store_true", help="只校验不写文件")
    parser.add_argument("--preview", action="store_true",
                        help="额外输出 tmp/imported-{skin}.png 总览图")
    args = parser.parse_args(argv)

    src_dir = Path(args.src)
    if not src_dir.is_dir():
        print(f"目录不存在：{src_dir}")
        return 2
    if args.skin not in ("cat", "dog", "monkey"):
        print(f"未知皮肤：{args.skin}（可选：cat, dog, monkey）")
        return 2
    if args.frames < 2:
        print(f"--frames 至少 2 帧，当前 {args.frames}")
        return 2
    if args.layout == "grid" and args.rows * args.cols < args.frames:
        print(f"grid 模式 rows×cols 不能少于 {args.frames} 帧，"
              f"当前 {args.rows}×{args.cols}")
        return 2

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    unknown = only - set(ACTION_NAMES)
    if unknown:
        print(f"未知动作：{', '.join(sorted(unknown))}"
              f"（可选：{', '.join(ACTION_NAMES)}）")
        return 2

    foot_line = (args.foot_line if args.foot_line is not None
                 else measure_foot_line(Path(args.out), args.skin))

    try:
        frames = import_skin(src_dir, args.skin, args.layout, args.rows, args.cols,
                             args.bg, args.tol, args.keep_extra, args.frames,
                             foot_line, only or None)
    except ValueError as exc:
        print(f"导入失败：{exc}")
        return 1

    if not frames:
        found = sorted(p.name for p in src_dir.rglob("*.png")) or ["（一张图也没找到）"]
        print(f"没有匹配到任何动作：目录名或文件名需含 {', '.join(ACTION_NAMES)}\n"
              f"找到的文件：{', '.join(found[:12])}")
        return 1
    # 指定了 --only 就只比这部分：没让导的动作不算缺，否则导 3 个动作报 10 个缺
    wanted = only or set(ACTION_NAMES)
    missing = [a for a in ACTION_NAMES if a not in frames and a in wanted]
    if missing:
        print(f"缺少动作素材：{', '.join(missing)}"
              f"（loader 会按相似动作回落，但目检前请补齐）")
    for line in report_motion(frames):
        print(f"提醒：{line}")
    if args.dry_run:
        print(f"校验通过：{args.skin}，{len(frames)} 个动作 × "
              f"{args.frames} 帧（未写文件）")
        return 0

    total = write_frames(Path(args.out), args.skin, frames, source=str(src_dir))
    print(f"已导入 {total} 帧 -> {Path(args.out) / 'pet' / args.skin}"
          f"（{len(frames)} 个动作，{SIZE}×{SIZE} RGBA，脚线 {foot_line:.0f}）")
    if args.preview:
        preview = write_preview(root / "tmp" / f"imported-{args.skin}.png", frames)
        print(f"目检预览：{preview}" if preview else "目检预览：无帧可拼，跳过")
    return 0


if __name__ == "__main__":
    sys.exit(main())