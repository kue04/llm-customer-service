"""RAG 接入与鉴权改造（数据接入 / 文档解析 / 切分 / 权限）。

模块划分：
- ``db``              引擎与会话工厂，连接串只从环境变量读取
- ``models``          SQLAlchemy 2.x 声明式模型，对应 Alembic 迁移 0001
- ``repository``      面向业务的事务性读写函数
- ``parsers``         解析契约与各格式解析器（阶段 2）
- ``parser_registry`` 来源类型判定与解析器注册表
- ``chunkers``        结构化切分：分组 / 超长段落 / 表格 / 列表 / 重叠 / parent-child（阶段 3.2）
- ``object_store``    对象存储接口与本地实现、对象 key 与 source_uri 寻址（2.3）
- ``content_sniff``   magic bytes 内容嗅探与「扩展名 / 内容一致性」判定（2.3）
- ``queue``           任务投递与消费原语（Redis Stream + 内存降级）（2.3 / 3.3）
- ``pipeline``        幂等处理流水线：八阶段状态流转、判重、失败可按阶段重试（3.3）
- ``worker``          队列消费者：把投递的 job 推到终态（3.3，补 F5 的消费端缺口）
- ``index_manifest``  索引 manifest 与原子切换 / 回滚的纯文件层（3.4）
- ``index_builder``   按 ordinal 重建 FAISS 索引并原子切换（3.4）
"""

from __future__ import annotations

__all__ = [
    "chunkers",
    "content_sniff",
    "db",
    "index_builder",
    "index_manifest",
    "models",
    "object_store",
    "parser_registry",
    "parsers",
    "pipeline",
    "queue",
    "repository",
    "worker",
]
