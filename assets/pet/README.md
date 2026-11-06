# 宠物序列帧素材

每个动作一个目录，目录内按文件名字典序播放（`01.png`、`02.png`…），
由 `FrameAnimator` 以 12 FPS 循环播放。

## 目录约定

```
assets/pet/
└── {skin}/            # 皮肤 id：cat / dog / monkey
    ├── idle/          # 待机
    ├── walk/          # 自主走动
    ├── hurt/          # 被戳反应
    ├── think/         # 等待 AI 应答
    ├── eat/           # 进食
    ├── speak/         # 气泡说话
    ├── sleep/         # 睡眠
    └── sad/           # 心情低落
```

皮肤 id 的合法取值来自 `domain.config_model.PET_SKINS`，首个 `cat` 为默认皮肤，
也是**回落皮肤**：某皮肤缺某动作的帧时，自动改用 `cat` 的同名动作，
避免切换形象后变成占位圆脸。当前皮肤由 `AppConfig.pet.skin` 决定，设置窗口可切换。

动作名与 `domain.state_machine.BehaviorState` 的映射见
`src/WisePet/ui/frame_animator.py` 的 `ACTION_BY_STATE`（拖拽复用 `idle`）。

## 素材规格

| 项 | 建议 |
|----|------|
| 尺寸 | 128×128 正方形（与画布 `CANVAS_SIZE` 一致，避免运行时缩放） |
| 格式 | PNG，透明背景 |
| 帧数 | 每动作 2~8 帧，首尾相接能循环 |
| 朝向 | 只画**朝右**一侧即可，朝左由程序水平镜像 |
| 命名 | 两位数序号 `01.png`…`99.png`，不要用 `1.png`（排序会错） |

## 缺失与降级

- 某动作目录不存在或没有可用 PNG → 自动改用程序绘制的占位圆脸
  （`PlaceholderRenderer`），并写 WARNING 日志，不崩溃（PRD US-1）。
- 单个文件损坏（无法被 Qt 解码）→ 该帧用占位帧替代。
- 尺寸不一致的帧 → 等比缩放后居中到 128×128 画布。

`cat` / `dog` / `monkey` 当前都是外部导入的 **扁平矢量贴纸风** AI 素材（干净深棕描边、纯色平涂、
白 / 米色口鼻胸腹与四爪、粉腮红、大圆眼、侧身朝右、**四足着地**）。角色分别是橘猫虎斑、柴犬卷尾、
小猴焦糖棕配米色脸盘，三套共用同一姿态体系（含四足对角步态），切皮肤不会跳画风。
导入过的动作目录带 `.imported` 标记：生成器默认跳过它们，避免 `--skin all` 把 AI 素材覆盖回程序化画风。
出图都是 256×256，由 `FrameAnimator` 等比缩到 128 画布。

AI 素材的导入链路：每个动作一张 2 行 3 列的六格连续分镜图，脚本抠白底、按内容包围盒等比缩放、
脚底对齐同一地平线、切成 256×256 RGBA 六帧：

```
python tools/import_pet_images.py --in tmp/ai/{skin} --skin {skin} --layout grid --rows 2 --cols 3 --frames 6 --bg cut --keep-extra --out assets --preview
```

重新生成程序化皮肤（加 `--force` 才会连带覆盖带 `.imported` 标记的皮肤）：

```
python tools/gen_pet_assets.py --skin all
```

检查某皮肤缺哪些动作：

```
python -c "from WisePet.infra.frame_loader import FrameAssetLoader; print(FrameAssetLoader('assets', 'dog').missing_actions())"
```
