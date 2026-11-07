# 桌面宠物 WisePet 需求设计 v1.22

- 版本：v1.22
- 状态：已实现（待评审）
- 日期：2026-11-07
- 性质：增量修订，仅描述相对 v1.21 的变化；不回改历史版本文件
- 对应技术设计：[NFR v1.23](../../NFR/v1.23/desktop-pet-tech-design-v1.23.md)

## 1. C39：聊天记录改用内嵌数据库，其余持久化保持不变

用户提出「JSON 是不是不太好，可以内嵌数据库」，并在方案评审后明确范围：
**只把聊天记录存进数据库，`config.json` / `pet_state.json` / `growth.json` /
`quiet.json` / `memory.json` / `weekly.json` / `reminders.json` / `focus.json` /
`onboarding.done` / 日志全部保持原样。**

### 为什么只迁这一类

| 数据 | 形态 | 判断 |
|---|---|---|
| 配置 / 状态 / 计数 | KB 级小文档、事件驱动、用户可读可手改 | 保持 JSON，`atomic_write` + 损坏备份已够 |
| 日志 | 文本追加 + 滚动 | 保持 `RotatingFileHandler` |
| **聊天记录** | **持续增长、语义只追加、需要跨日全文检索** | **迁 SQLite** |

旧实现的两条实证问题：`ChatLogRepository.append()` 每次「读全天 → 追加 → 整文件重写」，
是 O(n²)；上一版新增的 `ChatHistory.search()` 要全量扫所有按天文件、无索引。
单写者、无并发，SQLite 的事务/WAL 优势不是理由，**真追加与一次 SQL 检索才是**。

### 需求与验收

| 编号 | 需求 | 验收项 |
|---|---|---|
| R1 | 聊天记录落到 `~/.WisePet/wisepet.db` 单文件 | AC-DB-1：只产生一个 db 文件，运行时不再产生 `chat/*.json`；`sqlite3` 为标准库，无新依赖 |
| R2 | 保持按天回看与「不截断」语义 | AC-DB-2~3：`days()` 返回有记录的日期升序、`load(day)` 按插入顺序返回、25 条不截断、Unicode 完整 |
| R3 | 真追加，不再整文件重写 | AC-DB-4：一条记录一次 INSERT，写入代价与当天已有条数无关 |
| R4 | 跨日全文检索 | AC-DB-5：大小写不敏感、按时间倒序、`limit` 生效、空查询返回空；一次 SQL 完成 |
| R5 | 旧数据一条不丢、可回退 | AC-MIG-1~4：首次启动一次性导入 `chat/*.json`，成功后归档到 `chat/legacy/*.json.migrated`（**不删除**）；重复启动不产生重复记录；损坏的旧 JSON 仍走原有备份逻辑；真机数据只读副本验证 3 文件 113 条 → 入库 113 条 |
| R6 | 损坏与回退 | AC-MIG-5~6：db 损坏自动备份 `wisepet.db.bak-*` 并重建、不崩；把归档文件移回 `chat/` 并去掉 `.migrated` 后缀、删掉 db 重启即可重新导入 |
| R7 | 其余持久化零改动 | AC-KEEP-1：10 类非聊天数据仍为原来的 JSON 文件、路径与格式不变 |

## 2. 受影响的既有契约（已同步）

- `FR v1.8` AC-CHAT-5「删掉某天文件改成损坏 JSON，启动不崩且被备份」→ **物理文件不再产生**，
  等价能力由「db 损坏自动备份重建」承接，并在 `README.md` / `docs/设置手册.md` 明确写出
  「删某天文件即删记录」的旧做法不再适用。
- `README.md` 的「不想要就删对应日期的文件」、设置窗与输入条里 `~/.WisePet/chat/` 的文案，
  全部改成 `~/.WisePet/wisepet.db`。
- `tests/ui/test_main_assembly.py` 三条 FR v1.8 生命周期断言，从「读按天 JSON」改成
  「查 SQLite 仓储」，**语义（内容 / 角色 / 来源 / 重启不重复落盘）不变**。

## 3. 范围外

不迁 `focus.json`（用户要求其他保持不变）；不启用 WAL；不引入 FTS5（中文不分词）；
不删除旧 `ChatLogRepository`（保留作迁移与回退工具）；不做聊天记录的导出 / 删除 UI。
