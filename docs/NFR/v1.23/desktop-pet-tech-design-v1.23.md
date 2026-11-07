# 桌面宠物 WisePet 技术设计 v1.23

- 版本：v1.23
- 状态：已实现（待评审）
- 日期：2026-11-07
- 对应需求：[FR v1.22](../../FR/v1.22/desktop-pet-PRD-v1.22.md)（C39）
- 性质：落地登记，不回改历史版本

## 1. 落点

遵守 `ui → app → domain ← infra`；`sqlite3` 是标准库，domain 仍零三方依赖，
`requirements.txt` 不变。改动集中在 4 个文件 + 2 处 UI 文案，其余仓储一行未动。

| 文件 | 改动 |
|---|---|
| `src/WisePet/infra/sqlite_chat_repo.py`（新增） | `SqliteChatRepository`：`append/load/days/search/count`，与旧仓储同接口；内含建表、损坏自愈、旧 JSON 迁移与归档 |
| `src/WisePet/app/chat_history.py` | `search()` 优先委托 `store.search()`，无该方法时回退原全量遍历（保留 StubStore 与回退路径） |
| `main.py` | 装配类换成 `SqliteChatRepository(self._config_repo.path.parent)`，构造时自动完成一次性迁移 |
| `src/WisePet/ui/chat_window.py`、`chat_input.py` | 两处 `~/.WisePet/chat/` 文案改成 `~/.WisePet/wisepet.db` |
| `tests/integration/test_sqlite_chat_repo.py`（新增） | 11 项：插入顺序 / 按天分离 / 不截断 / Unicode / 单文件 / 检索 / 委托 / 迁移 / 幂等 / 损坏旧文件 / db 损坏重建 |
| `tests/ui/test_main_assembly.py` | 3 条 FR v1.8 生命周期断言改查 SQLite，语义不变 |

## 2. 表结构与读写

```sql
CREATE TABLE chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,                 -- day_key(ts)，沿用本地时区按天语义
    ts REAL NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    legacy_key TEXT UNIQUE            -- 迁移幂等：{原文件名}:{序号}
);
CREATE INDEX idx_chat_turns_day ON chat_turns(day, id);
CREATE INDEX idx_chat_turns_ts  ON chat_turns(ts DESC);
```

- `append()` 一条 `INSERT`（带 `legacy_key` 时用 `INSERT OR IGNORE`）；`load(day)` 按 `id`
  排序还原插入顺序；`days()` 用 `SELECT DISTINCT day ORDER BY day`。
- `search()` 用 `instr(lower(content), lower(?)) > 0 ORDER BY ts DESC, id DESC LIMIT ?`：
  中文靠 `instr` 做子串匹配（FTS5 默认不切中文），一次 SQL 完成，不再读一堆按天文件。
- 每次操作用 `contextlib.closing(sqlite3.connect(...))` 显式关闭连接；`timeout=5.0` 兜底。
- **不启用 WAL**：单写者用不上，且 WAL 会额外产生 `-wal` / `-shm`，破坏「单文件」承诺。

## 3. 迁移与自愈

1. `_ensure_schema()`：目录不存在则建；`executescript` 抛 `sqlite3.DatabaseError`
   → 把 db 改名 `wisepet.db.bak-{ts}`（顺带清 `-wal`/`-shm`）→ 重建表。
2. `_migrate_legacy()`：扫 `chat/*.json`（仅顶层，不含 `legacy/`）：
   - 解析失败 → 复用 `load_json_or_backup_reset()`，损坏文件改名 `*.bak-{ts}` 后跳过；
   - db 已有数据 → 只归档不重复导入；
   - 否则按 `records` 顺序逐条 `INSERT OR IGNORE`，`legacy_key = "{文件名}:{序号}"`；
   - 成功后 `rename` 到 `chat/legacy/{name}.json.migrated`，**不删除**。
3. 幂等：归档后顶层不再有 `*.json`，重复构造不会二次导入。

**回退路径**：把 `chat/legacy/*.json.migrated` 移回 `chat/`、去掉 `.migrated` 后缀、
删掉 `wisepet.db` 重启，即重新导入旧数据。

## 4. 否决方案

| 否决 | 理由 |
|---|---|
| 11 个文件全量迁库 | `config.json` 手改、`README` 删某天文件、FR v1.8 AC-CHAT-5 是用户可见契约；50~60 个测试要改写，而单写者 / KB 级数据下收益为零 |
| 启用 WAL | 单写者无并发，且多出两个文件 |
| FTS5 全文索引 | 默认不切中文，对子串检索无帮助 |
| 删掉旧 `ChatLogRepository` | 丢掉迁移读取与回退能力，既有 5 个集成用例也要重写 |
| 迁移后删除原 JSON | 用户数据不可逆 |
| 把 `focus.json` 一起迁 | 用户明确「其他保持不变」 |
| 迁移脚本单独手动执行 | 容易漏；构造时做且幂等 |

## 5. 验证

- 新增 11 项集成测试；既有 3 条生命周期断言改查库，其余 1206 项零改动通过。
- **真机数据只读副本**：3 个按天文件 / 113 条源记录 → `db.count()` 113 条、
  `days()` 为 `2026-11-05/06/07`、3 个文件全部归档；原目录未做任何写操作。
- 全量 `1209 passed`、覆盖率 `94.64%`（`fail_under = 90`，TOTAL 3750 stmts / 201 miss）、
  改动文件 lints 0。
- **真机待目检**：重启后历史窗能看到迁移前的聊天、顶部日期下拉仍有三天、
  搜索框能搜到旧记录、`~/.WisePet/` 下除新增 `wisepet.db` 与 `chat/legacy/` 外其余 JSON 不变。
