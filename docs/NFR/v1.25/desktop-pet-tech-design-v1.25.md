# 桌面宠物 WisePet 技术设计 v1.25

- 版本：v1.25
- 状态：已实现（待评审）
- 日期：2026-11-07
- 对应需求：[FR v1.24](../../FR/v1.24/desktop-pet-PRD-v1.24.md)（C41）
- 性质：落地登记，不回改历史版本

## 1. 落点

| 文件 | 改动 |
|---|---|
| `src/WisePet/domain/config_model.py` | 新增 `SystemSettings` dataclass + `AppConfig.system` 字段；`from_dict` 用 `_choice`（小写级别白名单）与 `_int_clamp`（1~100 MiB / 1~30 天）解析，`asdict` 自动落 `system` 分节 |
| `src/WisePet/infra/logger_setup.py` | `setup_logging(data_dir=None, level=None, system=None)`：`_effective_level()` 让显式 `level` 优先（后向兼容），其余取 `system.log_max_mb / log_retention_days / log_split_by_level`；新增 `_LEVELS` 映射与 `reconfigure_logging()` |
| `src/WisePet/ui/settings_dialog.py` | 第 7 个分区 `_build_system_group()`（级别下拉 + 2 个数值框 + 分档复选框 + note）；`_load_from` 用 `getattr` 兼容旧配置；`collect()` 用 `replace` 透传；新控件进滚轮 guard；`QLabel#sectionNote` QSS |
| `main.py` | `main()` 启动先 `JsonConfigRepository().load()` 再 `setup_logging(system=...)`；`apply_config()` 比较 `config.system != previous_system` 才 `reconfigure_logging()` 并留一行 INFO |
| 测试 | 新增 7 项；`test_settings_dialog.py` 分区数断言 6→7；其余既有测试零改动 |

## 2. 启动顺序这个关键约束

原来 `main()` 是「先 `setup_logging()` 装配 handler → 再构造 `PetApplication` 读配置」，
日志配置读不到 `system` 分节。改为：

```python
migrated = migrate_legacy_data()
boot_config = JsonConfigRepository().load()      # 轻量读一次，KB 级
log_file = setup_logging(system=boot_config.system)
...
pet_app = PetApplication(app)                    # 内部再读一次，无副作用
```

`setup_logging()` 保持「重复调用不叠加 handler」的幂等判断，所以两次读配置不会挂重复 handler。

## 3. 热重建

```python
def reconfigure_logging(system, data_dir=None) -> Path:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if (isinstance(handler, DatedRotatingFileHandler)
                or getattr(handler, "_wisepet_console", False)):
            root.removeHandler(handler)
            handler.close()
    return setup_logging(data_dir=data_dir, system=system)
```

- 只移除**自己挂的** handler：文件档靠 `isinstance`、控制台靠 `_wisepet_console` 标记，
  不碰第三方库可能挂在根 logger 上的 handler。
- `StreamHandler.close()` 不关闭底层 `sys.stderr`，重建安全。
- 错误档的最小级别取 `max(logging.WARNING, effective_level)`：根级别设为 ERROR 时
  不会出现「主档收 ERROR、错误档却收 WARNING」的倒挂。
- `apply_config()` 只在 `system` 真的变化时重建，避免改走速这类无关保存也去关开文件句柄。

## 4. 否决方案

| 否决 | 理由 |
|---|---|
| 参数塞进「宠物参数」/「陪伴进阶」 | 用户明确要独立分区；且这些不是宠物属性，混放会让分区语义越来越糊 |
| 每次 `apply_config` 都重建日志 | 无谓 IO，测试中反复触发，且可能打断正在写的日志 |
| 保存后要求重启才生效 | 与 `docs/设置手册.md` 开篇「所有设置保存后立即生效」的承诺冲突 |
| `level` 参数改成必填 / 删掉 | 破坏既有 `setup_logging(tmp_path)` 与 16 项日志测试的调用契约 |
| `logging.config.dictConfig` | 与现有 `SecretMaskFilter`、自定义 `DatedRotatingFileHandler`、热重建语义不一致 |
| `reconfigure_logging` 清空根 logger 全部 handler | 会误删第三方库挂的 handler |
| 把配置做成模块级全局单例 | 破坏 domain 零依赖与注入式可测性 |

## 5. 验证

- 新增 7 项回归：配置 roundtrip / 非法值钳制、日志参数应用到 handler、热重建不叠加 handler、
  分区回填、分区收集且不丢其它分节、`apply_config` 仅在 system 变化时重建。
- 全量 **1225 passed / 0 failures / 0 errors**（JUnit 报告），覆盖率 **94.41%**
  （`fail_under = 90`，3830 stmts / 214 miss），改动文件 lints 0。
- **真机待目检**：设置窗第 7 个「系统配置」分区可见且可滚动；把上限改 5 MiB、保留改 1 天、
  关掉分档后点保存，`WisePet-error.log` 不再新增、日志里出现 `system log settings applied`；
  重启后设置仍在。
