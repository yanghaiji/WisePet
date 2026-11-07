# 桌面宠物 WisePet 技术设计 v1.24

- 版本：v1.24
- 状态：已实现（待评审）
- 日期：2026-11-07
- 对应需求：[FR v1.23](../../FR/v1.23/desktop-pet-PRD-v1.23.md)（C40）
- 性质：落地登记，不回改历史版本

## 1. 落点

只改 `src/WisePet/infra/logger_setup.py` + 两个测试文件，`main.py` 与业务模块零改动。

| 文件 | 改动 |
|---|---|
| `src/WisePet/infra/logger_setup.py` | 新增 `ERROR_LOG_FILE_NAME` / `MAX_BYTES=10*1024*1024` / `RETENTION_DAYS=3` / `BACKUP_COUNT=200`、`_backup_paths()`、`DatedRotatingFileHandler`；`setup_logging()` 挂主档 + 错误档两个 handler |
| `tests/integration/test_logger_rotation.py`（新增） | 9 项：分级 / 幂等 / 阈值 / 真实滚动 / 过期删除 / 当前文件保留 / 未滚动也清 / 跨天封存+3 天后清理 / 两档独立 |
| `tests/integration/test_logger_setup.py` | 仅改幂等断言（RotatingHandler 1 → 2 个），其余 16 项原样通过 |

`mask_secrets()` / `SecretMaskFilter` / `_mask_value()` 完全未改，两档都挂同一个 Filter。

## 2. `DatedRotatingFileHandler`

继承 `RotatingFileHandler`，构造参数 `max_bytes / backup_count / retention_days / now_provider`
（`now_provider` 供测试注入时间，生产环境用 `time.time`）。

```python
def emit(self, record):
    day = int(self._now() // 86400.0)
    if self._last_purge_day != day:
        self.purge_expired()          # ① 先清过期备份
    if day != self._current_day:
        self._current_day = day
        if self.stream is not None:
            self.flush()
            self.doRollover()        # ② 再跨天封存「昨天」
    super().emit(record)
```

- `purge_expired()` 遍历 `_backup_paths(baseFilename)`（只认数字后缀 `.1`、`.2`…），
  按 `mtime` 判定 `age > 3 * 86400` 才删；**当前文件从不参与**，拿不到时间戳时保守保留。
- `getFilesToDelete()` 重写成同样的时间口径，避免 `backupCount` 在滚动时误删 3 天内的备份。
- **顺序是硬约束**：初版把 `purge_expired()` 放在 `doRollover()` 之后，跨天时刚封存的
  「昨天」文件立刻被当作过期删掉，测试 `should_seal_each_day_and_drop_content_older_than_three_days`
  抓到后改为「先清、再封存」。

### 为什么需要跨天强制封存

`RotatingFileHandler` 只在写满 10 MiB 时才滚动。桌宠日志量很小（真机 `WisePet.log` 316 KB），
若不做跨天封存，3 天前的内容会一直留在**正在写的当前文件**里，而清理逻辑按设计不能删当前文件
——「只保留近 3 天」就变成空话。跨天 `doRollover()` 把昨天内容封存成备份，3 天后由
`purge_expired()` 正常删除。

## 3. 分级口径

| 档 | 文件 | Handler level | 用途 |
|---|---|---|---|
| 主档 | `WisePet.log` | `level`（默认 INFO，`setup_logging(level=DEBUG)` 时含 DEBUG） | 完整时间线、状态切换、聊天来源 |
| 错误档 | `WisePet-error.log` | `WARNING` | 超时、异常、`protocol` 错误、降级告警 |

两档各自独立滚动与清理（前缀不同，`_backup_paths` 不会串台）。`root.setLevel(level)` 与
控制台 handler 保持原样；`setup_logging()` 仍返回主档路径，`main.py:1269` 无需改动。
幂等判断从「存在任一 `RotatingFileHandler`」改成「存在任一 `DatedRotatingFileHandler`」。

## 4. 否决方案

| 否决 | 理由 |
|---|---|
| `TimedRotatingFileHandler` | 只能按天滚动，无法同时满足「单文件超 10 MiB 就分割」 |
| 继续按 `backupCount=3` 保留 | 用户要按天，不是按文件数；且会误删 3 天内多次滚动的备份 |
| 四级互斥分文件（debug/info/warn/error 各一份） | 看完整时间线要拼四个文件，且所有读 `WisePet.log` 的既有断言/文档要重写 |
| 只加 level 字段不拆文件 | 排障仍要在海量 INFO 里翻 WARNING，没有解决分级诉求 |
| 跨天只清理不封存 | 当前文件里的旧内容删不掉，3 天保留不成立 |
| 清理时删除当前文件 | 会丢正在写的日志并让 handler 句柄失效 |

## 5. 验证

- 新增 9 项集成测试；既有 16 项日志测试只改一条幂等断言。
- 全量 **1218 passed / 0 failures / 0 errors**（JUnit 报告），覆盖率 **94.39%**
  （`fail_under = 90`，3800 stmts / 213 miss），改动文件 lints 0。
- **真机待目检**：重启后 `~/.WisePet/logs/` 出现 `WisePet-error.log`；制造一条 WARNING/ERROR
  后两个文件都有、INFO 只在主档；把旧备份 mtime 调到 4 天前，下次写日志时被删除；
  跨天后主档被封存成 `.1`。
