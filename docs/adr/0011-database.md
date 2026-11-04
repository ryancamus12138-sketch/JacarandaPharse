# 0011 数据库：SQLAlchemy 2.0 + Alembic，原始值与修改分表

- 状态：已采纳
- 日期：2026-11-04

## 背景

处理结果要持久保存，供以后的 API、审核页面和审计使用。表结构必须落实项目规则：
原始值不覆盖（2）、每次抽取记录来源（4）、数据带归属（6）、插件不碰数据库（7）。

## 决策

**四张表：**

| 表 | 内容 |
| --- | --- |
| documents | 一个文件：文件名、sha256、大小、页数、对象存储位置（下一步填写） |
| extractions | 一页的一次抽取：文字来源（ocr / pdf-text）、OCR 引擎、模型、后端、提示词版本、字段清单、用时、模型原始回答、规则校验结果（JSON） |
| extracted_values | 每个字段：值（统一存文字，按 type 还原）、模型说的行号、对齐状态、是否需要确认、原因、出处（JSON） |
| value_revisions | 人工修改：新值、原因。只新增 |

**约定：**

- 每张表继承 `OwnedMixin`（user_id、team_id）和 `TimestampMixin`（created_at，数据库填写）。
  个人模式使用固定的 `PERSONAL_OWNER`。
- 主键用 UUID。约束和索引按固定规则命名，方便迁移。
- `extracted_values` 写入后不修改；人工修改写入 `value_revisions`，最新一条为当前值。
- JSON 列在 PostgreSQL 上用 JSONB，可查询其中内容。
- 写入由 `pipeline/store.py` 完成；`session_scope()` 保证一次保存要么全部成功，要么全部回滚。
- 列名避开 SQL 关键字（`row` → `row_no`）。
- 时间带时区存储，数据库按 UTC 显示。

**迁移：** Alembic，配置在 `alembic.ini`，数据库地址从 `.env` 读取（不写进配置文件）。
生成迁移后自动用 ruff 整理。`alembic check` 用来发现模型改了但没生成迁移。

**测试：** 用内存 SQLite 测试保存和回滚逻辑，不连 PostgreSQL。

**授权：** SQLAlchemy（MIT）、Alembic（MIT）。

## 后果

- `jacara extract --save` 把结果写进数据库；可以用 SQL 按文件、模型、状态查询。
- 原始文件还没有保存（`storage_key` 为空），下一步上传功能存进对象存储。
- 以后加列、加表都要通过迁移，正式环境一般只升级不降级。
