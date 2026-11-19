# ADR-020 SQLite 数据库迁移

状态：已接受。补充 ADR-002。

## 决定

- SQLite 的持久 schema 由随包发布的 Alembic revision 管理。初版库通过已有的 schema 标记识别，先登记旧基线，再迁移到当前版本。
- 新库用当前的 SQLAlchemy metadata 建表，再登记当前 revision。SQLite 初始化及升级使用同一 BEGIN IMMEDIATE 事务，允许并发打开同一库。
- 迁移修改表结构。业务状态仍通过事件写入和投影重放产生。费用投影增加 cost_known，以前的记录沿用未知状态。
- Alembic 与 SQLAlchemy 一样只允许由 storage 层导入。
