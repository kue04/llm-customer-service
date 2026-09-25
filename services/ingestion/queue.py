"""接入任务队列（计划 2.3）。

为什么要有内存降级实现
----------------------
本机与 CI 都没有 Redis，但上传接口必须能返回 202 并把任务投出去。
因此把「投递」抽成接口，两种实现语义一致，切生产只改环境变量
（``RAG_REDIS_STREAM_URL``）。

**降级只发生在「没有配置 Redis」时。**
显式配置了 ``RAG_REDIS_STREAM_URL`` 却连不上（或没装 redis 客户端）时抛
:class:`QueueUnavailableError`，由上传接口翻成 **503**。
静默退回内存队列会制造「接口说任务已受理、其实任务不在任何队列里」这种情况：
进程一重启任务就消失，用户以为上传成功了 —— 属于最坏的一类故障。
没配置 Redis 则是明确的开发场景，此时内存队列是预期行为。

只做投递，不做消费
------------------
B4 只提供 ``publish`` 与可观测的 ``depth``。消费端（``read`` / ``ack`` 与消费者组）
由 3.3 的 worker 驱动 —— 但**实现放在这里**：消费者组、``XREADGROUP`` 的语义是
「这个队列怎么被消费」，与 worker「拿到 job 之后干什么」是两件事，
分开之后 worker 才能在内存队列与 Redis 之间无差别地工作。

消费语义（worker 依赖的三条约定）
---------------------------------
1. **投递即触发，job 表才是唯一真相**：消费者拿到 ``job_id`` 后必须回数据库读任务，
   队列里不携带任何业务状态。因此同一条消息被消费两次不会产生两套事实。
2. **``read`` 返回即"已投递到消费者手上"**：内存队列是 pop（取出即不在队列里），
   Redis 是 ``XREADGROUP`` 到消费者自己的 pending 列表。
3. **``ack`` 的时机由 worker 决定**：本模块只提供原语，不替调用方决定
   「处理成功才 ack 还是处理完就 ack」—— 那是 [D-10] 里拍板的事。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
import os
from threading import Lock
import uuid


STREAM_URL_ENV = "RAG_REDIS_STREAM_URL"
STREAM_KEY_ENV = "RAG_INGESTION_STREAM_KEY"
CONSUMER_GROUP_ENV = "RAG_INGESTION_CONSUMER_GROUP"

DEFAULT_STREAM_KEY = "rag:ingestion:jobs"
DEFAULT_CONSUMER_GROUP = "rag-workers"

#: 消息体里的字段名（Redis Stream 是 field-value 结构）
PAYLOAD_FIELD = "job_id"

#: 消费者组不存在时 Redis 返回的错误前缀（用于区分「组已存在」与其他真错误）
BUSYGROUP_MARKER = "BUSYGROUP"


@dataclass(frozen=True, slots=True)
class QueueMessage:
    """一条待处理的投递记录。``message_id`` 是 ack 的凭据，``job_id`` 是业务键。"""

    message_id: str
    job_id: str


class QueueError(RuntimeError):
    """队列用法错误。"""


class QueueUnavailableError(QueueError):
    """队列不可用（配置了 Redis 但连不上 / 缺客户端）。调用方应返回 503。"""


class IngestionQueue(ABC):
    """任务投递接口。"""

    #: 实现名（日志与错误消息里用）
    name: str = "abstract"

    @abstractmethod
    def publish(self, job_id: str) -> str:
        """投递一个 job，返回消息 ID。"""

    @abstractmethod
    def depth(self) -> int:
        """当前待处理消息数（可观测性；Redis 用 ``XLEN``）。"""

    def read(self, *, count: int = 1, block_ms: int | None = None) -> list[QueueMessage]:
        """读取一批消息。**默认实现明确报错**，而不是返回空列表。

        返回空列表会让"这个队列不支持消费"与"当前没有消息"变得无法区分，
        而 worker 会据此安静地空转 —— 这属于最坏的一类静默失败
        （任务永远停在 ``pending``，而且日志上看不出任何异常）。
        """

        raise QueueError(f"{self.name} 队列实现不支持消费（未实现 read）")

    def ack(self, message_id: str) -> None:
        """确认一条消息已被处理。内存队列无需 ack，因此默认为空操作。"""

        return None



class InMemoryIngestionQueue(IngestionQueue):
    """进程内队列（无 Redis 时的开发降级实现）。

    队列生命周期与进程一致：**重启即丢**。这是刻意的取舍 ——
    与其宣称「持久化」却做不到，不如让它在名字与文档里都明确是开发用途。
    """

    name = "memory"

    def __init__(self) -> None:
        self._messages: deque[tuple[str, str]] = deque()
        self._lock = Lock()

    def publish(self, job_id: str) -> str:
        value = str(job_id or "").strip()
        if not value:
            raise QueueError("job_id 不能为空")
        message_id = uuid.uuid4().hex
        with self._lock:
            self._messages.append((message_id, value))
        return message_id

    def depth(self) -> int:
        with self._lock:
            return len(self._messages)

    def read(self, *, count: int = 1, block_ms: int | None = None) -> list[QueueMessage]:
        """取出最多 ``count`` 条消息（FIFO）。

        内存队列**取出即消失**（取出后 ``depth`` 立刻减少），因此不存在
        "重投"的可能；``block_ms`` 被忽略 —— 阻塞等待在多进程下没有意义，
        worker 用轮询代替（见 ``worker.IngestionWorker.run_forever``）。
        """

        size = max(int(count), 0)
        if size == 0:
            return []
        with self._lock:
            messages: list[QueueMessage] = []
            for _ in range(min(size, len(self._messages))):
                message_id, job_id = self._messages.popleft()
                messages.append(QueueMessage(message_id=message_id, job_id=job_id))
            return messages

    def pending_job_ids(self) -> list[str]:
        """当前待处理的 job_id 列表（按投递顺序）。仅供测试与本地调试。"""

        with self._lock:
            return [job_id for _message_id, job_id in self._messages]


class RedisStreamIngestionQueue(IngestionQueue):
    """Redis Stream 实现（正式实现，计划 2.3）。

    ``client`` 可注入：测试用一个假客户端断言 ``XADD`` 的参数，
    这样即使 CI 没装 redis 也能覆盖这条路径。
    """

    def __init__(
        self,
        url: str,
        *,
        stream_key: str = DEFAULT_STREAM_KEY,
        consumer_group: str = DEFAULT_CONSUMER_GROUP,
        consumer_name: str = "worker-1",
        client: object | None = None,
    ) -> None:
        self.url = str(url or "").strip()
        if not self.url:
            raise QueueError("Redis Stream URL 不能为空")
        self.stream_key = stream_key or DEFAULT_STREAM_KEY
        self.consumer_group = consumer_group or DEFAULT_CONSUMER_GROUP
        # 消费者名写进 pending 列表，排查"消息被谁拿走了"时是唯一线索
        self.consumer_name = str(consumer_name or "worker-1")
        self._client = client
        self._group_ready = False

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"redis-stream:{self.stream_key}"

    def _resolve_client(self) -> object:
        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as error:  # pragma: no cover - 由测试用 sys.modules 模拟
            raise QueueUnavailableError(
                "已配置 RAG_REDIS_STREAM_URL，但缺少 redis 客户端；请安装 requirements.txt 里的 redis"
            ) from error
        self._client = redis.Redis.from_url(self.url, decode_responses=True)
        return self._client

    def publish(self, job_id: str) -> str:
        value = str(job_id or "").strip()
        if not value:
            raise QueueError("job_id 不能为空")
        client = self._resolve_client()
        try:
            message_id = client.xadd(self.stream_key, {PAYLOAD_FIELD: value})  # type: ignore[attr-defined]
        except QueueUnavailableError:
            raise
        except Exception as error:
            # 这里刻意宽catch：没装 redis 时连 redis 的异常基类都 import 不到，
            # 因此无法按类型区分「连不上」与「命令被拒」，一律归为队列不可用。
            raise QueueUnavailableError(f"投递任务到 {self.stream_key} 失败：{error}") from error
        return str(message_id)

    def depth(self) -> int:
        client = self._resolve_client()
        try:
            return int(client.xlen(self.stream_key))  # type: ignore[attr-defined]
        except QueueUnavailableError:
            raise
        except Exception as error:
            raise QueueUnavailableError(f"读取 {self.stream_key} 长度失败：{error}") from error

    # ------------------------------------------------------------ 消费端

    def ensure_group(self) -> None:
        """创建消费者组（幂等）。

        ``id="0"`` 而不是 ``"$"``：``"$"`` 表示"只消费建组之后的新消息"，
        这会让**建组之前已经投递的消息永远没人处理**（上传成功、任务永远 pending）。
        用 ``"0"`` 从头消费 —— 这正是本项目最需要的语义：
        任务可能在上传时就投递了，而 worker 是后来才启动的。
        重复投递同一 job 由流水线自己的幂等保证（不是靠队列"只投一次"）。
        """

        client = self._resolve_client()
        try:
            client.xgroup_create(self.stream_key, self.consumer_group, id="0", mkstream=True)  # type: ignore[attr-defined]
            self._group_ready = True
        except Exception as error:  # noqa: BLE001 - 没装 redis 时连异常基类都 import 不到
            if BUSYGROUP_MARKER in str(error):
                self._group_ready = True
                return
            raise QueueUnavailableError(
                f"创建消费者组 {self.consumer_group}@{self.stream_key} 失败：{error}"
            ) from error

    def read(self, *, count: int = 1, block_ms: int | None = None) -> list[QueueMessage]:
        """``XREADGROUP`` 读一批未投递过的消息（``>`` 表示"从没给过任何消费者的"）。"""

        if not self._group_ready:
            self.ensure_group()
        client = self._resolve_client()
        options: dict[str, object] = {"count": max(int(count), 1)}
        if block_ms is not None:
            options["block"] = int(block_ms)
        try:
            response = client.xreadgroup(  # type: ignore[attr-defined]
                self.consumer_group,
                self.consumer_name,
                {self.stream_key: ">"},
                **options,
            )
        except QueueUnavailableError:
            raise
        except Exception as error:
            raise QueueUnavailableError(f"从 {self.stream_key} 读取任务失败：{error}") from error
        return _parse_read_response(response)

    def ack(self, message_id: str) -> None:
        """``XACK``：把消息从消费者的 pending 列表移除。"""

        client = self._resolve_client()
        try:
            client.xack(self.stream_key, self.consumer_group, str(message_id))  # type: ignore[attr-defined]
        except QueueUnavailableError:
            raise
        except Exception as error:
            raise QueueUnavailableError(f"确认消息 {message_id} 失败：{error}") from error


def _as_text(value: object) -> str:
    """把 Redis 返回的值统一成 ``str``。

    ``redis.Redis.from_url(..., decode_responses=True)`` 会直接给 str，
    但**注入的客户端**（以及 ``decode_responses=False`` 的配置）会给 ``bytes``。
    此前用 ``str(value)`` 转换，bytes 会变成 ``"b'1-1'"`` 这种带前缀的字符串 ——
    消息 id 被污染后 ``XACK`` 永远确认不掉，pending 列表只增不减，
    而日志上看不出任何异常（踩坑记录里的"静默失效"同类）。
    """

    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _parse_read_response(response: object) -> list[QueueMessage]:
    """把 ``XREADGROUP`` 的返回结构压成 :class:`QueueMessage` 列表。

    形状：``[[stream_key, [(message_id, {field: value}), ...]], ...]``；
    没有消息时 Redis 返回 ``None``（阻塞超时）或空列表。
    这里对形状宽容解析，但对**字段名也做 bytes/str 归一**：
    用 ``decode_responses=False`` 的客户端时字段名是 ``b"job_id"``，
    若直接按 str 去查会取不到值，于是消息会被**静默丢弃** ——
    "队列里有消息但任务不动"是最难排查的一类故障。
    """

    if not response:
        return []
    messages: list[QueueMessage] = []
    for _stream_key, entries in response:  # type: ignore[misc]
        for message_id, fields in entries:
            job_id = ""
            if isinstance(fields, Mapping):
                for key, value in fields.items():
                    if _as_text(key) == PAYLOAD_FIELD:
                        job_id = _as_text(value).strip()
                        break
            if job_id:
                messages.append(QueueMessage(message_id=_as_text(message_id), job_id=job_id))
    return messages


def build_queue(env: Mapping[str, str] | None = None) -> IngestionQueue:
    """按环境变量构造队列实现（纯函数，测试可直接注入 env）。"""

    source = os.environ if env is None else env
    url = (source.get(STREAM_URL_ENV) or "").strip()
    if not url:
        return InMemoryIngestionQueue()
    return RedisStreamIngestionQueue(
        url,
        stream_key=(source.get(STREAM_KEY_ENV) or "").strip() or DEFAULT_STREAM_KEY,
        consumer_group=(source.get(CONSUMER_GROUP_ENV) or "").strip() or DEFAULT_CONSUMER_GROUP,
    )


_QUEUE_CACHE: IngestionQueue | None = None


def get_queue() -> IngestionQueue:
    """FastAPI 依赖：进程内缓存一个队列实例。

    必须缓存：内存队列若每次请求都新建，投递进去的消息会随请求一起消失。
    """

    global _QUEUE_CACHE
    if _QUEUE_CACHE is None:
        _QUEUE_CACHE = build_queue()
    return _QUEUE_CACHE


def reset_queue() -> None:
    """清掉队列缓存（测试用；改过环境变量后必须调用）。"""

    global _QUEUE_CACHE
    _QUEUE_CACHE = None


def publish_job(job_id: str, *, queue: IngestionQueue | None = None) -> str:
    """把 job 投递到队列（默认使用进程内缓存的实现）。"""

    return (queue or get_queue()).publish(job_id)


__all__ = [
    "BUSYGROUP_MARKER",
    "CONSUMER_GROUP_ENV",
    "DEFAULT_CONSUMER_GROUP",
    "DEFAULT_STREAM_KEY",
    "PAYLOAD_FIELD",
    "STREAM_KEY_ENV",
    "STREAM_URL_ENV",
    "IngestionQueue",
    "InMemoryIngestionQueue",
    "QueueError",
    "QueueMessage",
    "QueueUnavailableError",
    "RedisStreamIngestionQueue",
    "build_queue",
    "get_queue",
    "publish_job",
    "reset_queue",
]
