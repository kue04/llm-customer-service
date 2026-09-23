"""RAG 接入与鉴权改造（数据接入 / 文档解析 / 切分 / 权限）。

模块划分：
- ``db``              引擎与会话工厂，连接串只从环境变量读取
- ``models``          SQLAlchemy 2.x 声明式模型，对应 Alembic 迁移 0001
- ``repository``      面向业务的事务性读写函数
- ``parsers``         解析契约与各格式解析器（阶段 2）
- ``parser_registry`` 来源类型判定与解析器注册表
"""

from __future__ import annotations

__all__ = ["db", "models", "parser_registry", "parsers", "repository"]
