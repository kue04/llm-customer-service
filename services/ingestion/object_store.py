"""对象存储（计划 2.3）。

职责边界
--------
解析器只吃 ``bytes``，因此「文件本体放在哪、怎么取回来」必须由接入层决定 ——
这里就是那一层。``ObjectStore`` 是接口，开发环境用 ``LocalObjectStore``
（本地目录），生产换 S3 兼容实现只需改 ``RAG_OBJECT_STORE_DRIVER``。

**回读路径必须与写路径同源**：``get()`` 返回**原始字节**（不做 base64、不解压），
因为流水线（3.3）会把取回的字节直接交给 ``parse_document(source, filename)``。
任何「顺手转成文本」的实现都会让解析结果与上传内容对不上。

两个寻址概念必须分清
--------------------
混在一起会同时破坏「重传追加版本」与「字节不被覆盖」两件事，因此显式分成两个：

* ``source_uri``（**逻辑标识**，落在 ``documents.source_uri`` 上）
  = ``upload://<净化后的文件名>``。
  同一租户内「同名 = 同一份文档」，重传即给这份文档追加版本
  （对应 [D-1] 与 ``test_reupload_same_source_uri_appends_version_not_new_document``）。
  它是稳定的，因此不能被用来定位某一次上传的字节。
* ``object key``（**物理位置**，每次上传一个）
  = ``rag/<tenant_id>/<document_id>/<净化后的文件名>``，
  含 tenant 路径与文档 ID（计划 2.3 原文要求）。

对象 key 为什么可直接由 (tenant, document_id, filename) 推导
-----------------------------------------------------------
流水线（3.3）拿到的是 ``job`` 与 ``document``，它需要知道去哪读字节。
把 key 做成这三个值的确定函数，就不必在 ``ingestion_jobs`` 上加一列
（加列要走 Alembic 迁移），也不会出现「任务与字节的对应关系只存在于内存里」。
同一个文档重复上传时 key 相同 —— 这符合「同一逻辑源的新修订」语义，
版本历史由 ``document_versions.content_hash`` 与内容 hash 唯一约束保证。

文件名只作展示
--------------
``sanitize_filename`` 会剥掉目录分隔符、控制字符与 ``..``，
因此客户端传 ``../../etc/passwd`` 只会得到 ``passwd``；
``LocalObjectStore`` 落盘前还会再校验一次最终路径确实在根目录内。
**对象 key 与文件名永远不会被拼接进 shell 命令。**
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
import os
from pathlib import Path
import re
import shutil
from typing import BinaryIO
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DRIVER_ENV = "RAG_OBJECT_STORE_DRIVER"
ROOT_ENV = "RAG_OBJECT_STORE_LOCAL_ROOT"

#: 默认驱动：本地目录（开发环境）
DEFAULT_DRIVER = "local"
#: 默认根目录（相对仓库根），与 .env.example 保持一致
DEFAULT_ROOT = "data/object_store"

#: 对象 key 的固定前缀，避免与根目录下其他文件混淆
KEY_PREFIX = "rag"
#: ``documents.source_uri`` 的 scheme
SOURCE_URI_SCHEME = "upload"
#: source_uri 与对象 key 里文件名的最大长度（过长的名字会撑爆路径长度限制）
MAX_FILENAME_LENGTH = 128

_COPY_CHUNK_BYTES = 1024 * 1024
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class ObjectStoreError(RuntimeError):
    """对象存储不可用或用法错误（找不到对象、key 非法、驱动未实现等）。"""


def sanitize_filename(name: str) -> str:
    """把客户端文件名净化成**只作展示**的安全片段。

    处理顺序：取最后一个路径片段 → 去控制字符 → 去危险字符 → 收敛长度。
    结果保证：非空、不含 ``/``、``\\``、不含 ``..``、不含首尾空白与点。
    空输入或全部被剥掉时回退为 ``"upload"``（调用方永远拿到可用的名字）。
    """

    raw = str(name or "")
    # 客户端可能传 Windows 风格路径，两种分隔符都要当分隔符看
    raw = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    raw = _CONTROL_CHARS_RE.sub("", raw)
    # 只保留字母数字、CJK、点、下划线、连字符；其余（含引号、管道、分号）一律替换为下划线
    cleaned = re.sub(r"[^\w.\-]", "_", raw, flags=re.UNICODE).strip()
    cleaned = cleaned.strip(". ")
    if not cleaned:
        return "upload"
    if len(cleaned) > MAX_FILENAME_LENGTH:
        # 保留扩展名，截断主干，避免把 .pdf 截掉导致下游认不出类型
        suffix = Path(cleaned).suffix[:16]
        keep = MAX_FILENAME_LENGTH - len(suffix)
        cleaned = cleaned[:keep] + suffix
    return cleaned or "upload"


def build_object_key(tenant_id: str, document_id: str, filename: str) -> str:
    """拼对象 key：``rag/<tenant_id>/<document_id>/<净化后的文件名>``。

    tenant 与 document_id 都是服务端生成的 32 位 hex；文件名走
    :func:`sanitize_filename`，因此本函数**不可能**产出越界路径。
    """

    return f"{KEY_PREFIX}/{tenant_id}/{document_id}/{sanitize_filename(filename)}"


def logical_source_uri(filename: str) -> str:
    """同一租户内同一文件名对应的稳定逻辑标识（见模块 docstring）。"""

    return f"{SOURCE_URI_SCHEME}://{sanitize_filename(filename)}"


def filename_from_source_uri(source_uri: str) -> str | None:
    """从 ``source_uri`` 反解文件名；不是本模块生成的 URI 时返回 ``None``。

    流水线（3.3）用 ``(tenant_id, document_id, filename)`` 重建对象 key，
    因此这条往返关系是一条契约，测试里做了闭环断言。
    """

    value = str(source_uri or "")
    prefix = f"{SOURCE_URI_SCHEME}://"
    if not value.startswith(prefix):
        return None
    remainder = value[len(prefix):].strip()
    return remainder or None


class ObjectStore(ABC):
    """对象存储接口。四个方法即够用，且便于替换成 S3 实现。"""

    #: 驱动名（日志与错误消息里用）
    name: str = "abstract"

    @abstractmethod
    def put(self, key: str, data: bytes | bytearray | BinaryIO, *, content_type: str | None = None) -> str:
        """写入对象并返回 key。``data`` 可以是 ``bytes`` 或二进制文件对象。"""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """读回**原始字节**。对象不存在时抛 :class:`ObjectStoreError`。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """对象是否存在（不读内容，避免为了判存在而把大文件读进内存）。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除对象。对象不存在时视为成功（幂等），便于重试。"""


class LocalObjectStore(ObjectStore):
    """本地目录实现（开发环境）。

    * 落盘前校验最终路径确实位于根目录内（防 ``..`` 与绝对路径逃逸）；
    * 写入先落临时文件再 ``os.replace``，避免半截文件被流水线读到；
    * ``content_type`` 只记录到旁边的 ``.meta`` 文件，不参与内容本身。
    """

    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        if not self.root.is_absolute():
            self.root = PROJECT_ROOT / self.root
        self.root = self.root.resolve()

    # ------------------------------------------------------------ 内部工具

    def _path_for(self, key: str) -> Path:
        value = str(key or "").strip()
        if not value:
            raise ObjectStoreError("对象 key 不能为空")
        if value.startswith(("/", "\\")) or "\\" in value:
            raise ObjectStoreError(f"对象 key 必须是相对 POSIX 路径，收到 {key!r}")
        if any(segment in {"", ".", ".."} for segment in value.split("/")):
            raise ObjectStoreError(f"对象 key 含非法路径片段，收到 {key!r}")
        candidate = (self.root / value).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ObjectStoreError(f"对象 key 越出存储根目录，收到 {key!r}")
        return candidate

    # ------------------------------------------------------------ 接口实现

    def put(self, key: str, data: bytes | bytearray | BinaryIO, *, content_type: str | None = None) -> str:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            if isinstance(data, (bytes, bytearray, memoryview)):
                temp_path.write_bytes(bytes(data))
            elif hasattr(data, "read"):
                with temp_path.open("wb") as handle:
                    shutil.copyfileobj(data, handle, _COPY_CHUNK_BYTES)  # type: ignore[arg-type]
            else:
                raise ObjectStoreError(f"不支持的数据类型：{type(data)!r}")
            os.replace(temp_path, path)
        except ObjectStoreError:
            temp_path.unlink(missing_ok=True)
            raise
        except OSError as error:
            temp_path.unlink(missing_ok=True)
            raise ObjectStoreError(f"写入对象失败：{key}（{error}）") from error

        if content_type:
            # 只作旁证，不参与内容；写失败不影响主流程
            try:
                path.with_name(f".{path.name}.meta").write_text(str(content_type), encoding="utf-8")
            except OSError:  # pragma: no cover - 磁盘满等极端情况
                pass
        return key

    def get(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as error:
            raise ObjectStoreError(f"对象不存在：{key}") from error
        except OSError as error:  # pragma: no cover - 权限等极端情况
            raise ObjectStoreError(f"读取对象失败：{key}（{error}）") from error

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as error:  # pragma: no cover - 权限等极端情况
            raise ObjectStoreError(f"删除对象失败：{key}（{error}）") from error
        path.with_name(f".{path.name}.meta").unlink(missing_ok=True)


def build_object_store(env: Mapping[str, str] | None = None) -> ObjectStore:
    """按环境变量构造对象存储实现（纯函数，测试可直接注入 env）。"""

    source = os.environ if env is None else env
    driver = (source.get(DRIVER_ENV) or "").strip().lower() or DEFAULT_DRIVER

    if driver != DEFAULT_DRIVER:
        # 不静默退回本地驱动：生产环境配错驱动却把文件写在本机磁盘上，
        # 表现为「上传成功但别的实例读不到」，比启动失败难排查得多。
        raise ObjectStoreError(f"暂不支持的对象存储驱动 {driver!r}，当前只实现了 {DEFAULT_DRIVER!r}")

    root = (source.get(ROOT_ENV) or "").strip() or DEFAULT_ROOT
    return LocalObjectStore(root)


def get_object_store() -> ObjectStore:
    """FastAPI 依赖：按环境变量返回对象存储。

    每次调用都重新读环境变量（构造开销只有一个 ``Path``），
    这样测试里改环境变量立即生效，不需要清理缓存。
    """

    return build_object_store()


__all__ = [
    "DEFAULT_DRIVER",
    "DEFAULT_ROOT",
    "DRIVER_ENV",
    "KEY_PREFIX",
    "ROOT_ENV",
    "SOURCE_URI_SCHEME",
    "LocalObjectStore",
    "ObjectStore",
    "ObjectStoreError",
    "build_object_key",
    "build_object_store",
    "filename_from_source_uri",
    "get_object_store",
    "logical_source_uri",
    "sanitize_filename",
]
