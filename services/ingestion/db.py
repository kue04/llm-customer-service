"""数据库引擎与会话工厂。

设计要点
--------
1. 连接串**只从环境变量** ``RAG_DATABASE_URL`` 读取，代码里没有任何硬编码凭据。
   默认值指向本地 SQLite 文件，便于在没有 Docker / PostgreSQL 的机器上开发；
   生产环境把该变量设为 ``postgresql+psycopg://user:pass@host:5432/db`` 即可切换，
   业务代码与迁移脚本不需要改动（迁移按方言无关写法编写）。
2. SQLite 默认**不启用外键约束**，这会让「跨租户外键约束」这类测试形同虚设。
   因此这里为 SQLite 连接统一打开 ``PRAGMA foreign_keys=ON``。
3. 会话用完即关，不在模块级持有 Session，避免测试之间互相污染。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATABASE_URL_ENV = "RAG_DATABASE_URL"
DEFAULT_DATABASE_URL = f"sqlite:///{(PROJECT_ROOT / 'data' / 'rag_metadata.db').as_posix()}"


def get_database_url() -> str:
    """返回当前生效的连接串（环境变量优先）。"""

    value = (os.getenv(DATABASE_URL_ENV) or "").strip()
    return value or DEFAULT_DATABASE_URL


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """SQLite 需要在每个连接上显式打开外键约束。"""

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """按连接串创建引擎，并挂上 SQLite 外键补丁。"""

    resolved = (url or get_database_url()).strip()
    if is_sqlite(resolved):
        # 提前建目录，否则 sqlite 会因为父目录不存在而报 unable to open database file
        prefix = "sqlite:///"
        if resolved.startswith(prefix):
            raw_path = resolved[len(prefix):]
            if raw_path and raw_path != ":memory:":
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)

    connect_args: dict = {}
    if is_sqlite(resolved):
        connect_args["check_same_thread"] = False

    engine = create_engine(resolved, echo=echo, future=True, connect_args=connect_args)
    if is_sqlite(resolved):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


_ENGINE_CACHE: dict[str, Engine] = {}
_SESSION_FACTORY_CACHE: dict[str, sessionmaker[Session]] = {}


def get_engine(url: str | None = None) -> Engine:
    """按连接串缓存引擎，避免每次调用重复建连接池。"""

    resolved = (url or get_database_url()).strip()
    engine = _ENGINE_CACHE.get(resolved)
    if engine is None:
        engine = create_db_engine(resolved)
        _ENGINE_CACHE[resolved] = engine
    return engine


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    resolved = (url or get_database_url()).strip()
    factory = _SESSION_FACTORY_CACHE.get(resolved)
    if factory is None:
        factory = sessionmaker(bind=get_engine(resolved), expire_on_commit=False, future=True)
        _SESSION_FACTORY_CACHE[resolved] = factory
    return factory


def dispose_engines() -> None:
    """释放所有缓存的引擎（测试收尾用）。"""

    for engine in _ENGINE_CACHE.values():
        engine.dispose()
    _ENGINE_CACHE.clear()
    _SESSION_FACTORY_CACHE.clear()


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """事务边界：正常提交，异常回滚。"""

    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
