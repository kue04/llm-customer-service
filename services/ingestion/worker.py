"""接入任务消费者（计划 3.3 的 worker）。

它补的是 F5 那个缺口
--------------------
B4 交付后 `queue.publish()` 已就绪，但**没有消费端** —— 上传成功、任务却永远停在
``pending``。F5 的结论是「上传接口实现了」容易被误读成「上传到入库闭环完成了」。
本模块就是那条闭环的另一半：把队列里的 ``job_id`` 变成真正被推进的任务。

两个关键取舍（都在台账写了决策记录）
------------------------------------
**1. 队列是"触发信号"，不是真相来源（[D-10]）。**
消息里只有 ``job_id``；worker 拿到后**回数据库读任务**，任务状态、阶段、错误码
全都在 ``ingestion_jobs`` 上。因此：

* 同一条消息被消费两次不会产生两套事实（第二次走到流水线里靠幂等收敛）；
* 消息丢失也只影响"这次没被及时处理"，任务仍在 ``pending``，可被重投；
* 队列不需要实现"恰好一次"，而这个要求本来也做不到。

**2. 处理完（无论成功失败）就 ack。**
不 ack 的话 Redis 会把消息一直留在 pending 列表里，靠 ``XAUTOCLAIM`` 才能回到别人手上 ——
而"重投"在本系统里等于再跑一遍流水线：失败原因（配置非法、文件丢了、模型不可用）
不会因为重投而消失，只会让同一个失败被反复重跑。**恢复动作交给 ``/reprocess``**：
它建一个新任务、带明确的审计记录，比"队列默默重试"更可查、也更可控。

一个任务失败**不能**影响后面的任务
----------------------------------
每个 job 用独立的数据库会话；异常一律被收敛成任务上的 ``error_code``
（见 ``pipeline.process_job``）。worker 的循环只做三件事：
取消息 → 处理 → ack，绝不因为单个任务的异常退出。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
import signal
import sys
import time
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from config.chunking_config import CHUNK_CONFIG
from services.ingestion import db, repository
from services.ingestion.object_store import ObjectStore, get_object_store
from services.ingestion.pipeline import PipelineOutcome, process_job
from services.ingestion.queue import (
    IngestionQueue,
    QueueMessage,
    QueueUnavailableError,
    get_queue,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WorkerStats:
    """一轮消费的统计。"""

    consumed: int = 0
    succeeded: int = 0
    failed: int = 0
    duplicates: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "consumed": self.consumed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
        }


class IngestionWorker:
    """从队列取任务并推进到终态。

    ``session_factory`` 可注入：测试用临时 SQLite，生产用
    :func:`services.ingestion.db.get_session_factory`。
    """

    def __init__(
        self,
        *,
        queue: IngestionQueue,
        session_factory: sessionmaker[Session] | None = None,
        store: ObjectStore | None = None,
        consumer_name: str = "worker-1",
        batch: int = 4,
        pipeline_options: dict[str, Any] | None = None,
    ) -> None:
        self.queue = queue
        self.session_factory = session_factory or db.get_session_factory()
        self.store = store or get_object_store()
        self.consumer_name = str(consumer_name or "worker-1")
        self.batch = max(int(batch), 1)
        self.pipeline_options = dict(pipeline_options or {})
        # Redis 队列的消费者名要在建组/读消息前设好，否则 pending 列表里认不出是谁拿走的
        if hasattr(self.queue, "consumer_name"):
            self.queue.consumer_name = self.consumer_name  # type: ignore[attr-defined]

    # ------------------------------------------------------------ 单轮

    def run_once(self, *, limit: int | None = None, block_ms: int | None = None) -> list[PipelineOutcome]:
        """读一批消息并逐个处理，返回每个任务的结果。队列不可用会抛 ``QueueUnavailableError``。"""

        count = max(int(limit if limit is not None else self.batch), 1)
        messages = self.queue.read(count=count, block_ms=block_ms)
        outcomes: list[PipelineOutcome] = []
        for message in messages:
            outcomes.append(self._handle(message))
        return outcomes

    def run_forever(
        self,
        *,
        iterations: int | None = None,
        poll_interval: float = 1.0,
        stop_event: Any | None = None,
        install_signal_handlers: bool = False,
    ) -> WorkerStats:
        """循环消费直到 ``iterations`` 轮、或收到停止信号。

        ``iterations`` 存在的唯一理由是让测试与"跑一轮就退出"的场景可终止；
        生产用 ``python -m services.ingestion.worker``（不传即无限循环）。

        空轮不忙等：``poll_interval`` 秒后再读。Redis 那边可以用
        ``block_ms`` 让服务端帮忙阻塞，但内存队列做不到 —— 统一成轮询
        让两种实现的行为一致（差异只应存在于队列内部）。
        """

        totals = [0, 0, 0, 0, 0]
        stop = {"flag": False}
        if install_signal_handlers:
            self._install_signal_handlers(stop)

        rounds = 0
        while not stop["flag"]:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                break
            try:
                outcomes = self.run_once()
            except QueueUnavailableError as error:
                # 队列不可用是**环境故障**，不是任务的失败：记日志后继续等，
                # 让 worker 成为一个"能自愈的后台进程"，而不是一崩就要人重启。
                logger.error("[worker] 队列不可用，%s 秒后重试：%s", poll_interval, error)
                outcomes = []
                time.sleep(max(poll_interval, 0.05))
            rounds += 1
            for outcome in outcomes:
                totals[0] += 1
                if outcome.skipped:
                    totals[4] += 1
                elif outcome.failed:
                    totals[2] += 1
                elif outcome.duplicate:
                    totals[3] += 1
                elif outcome.succeeded:
                    totals[1] += 1
            if iterations is not None and rounds >= iterations:
                break
            if not outcomes:
                time.sleep(max(poll_interval, 0.0))

        return WorkerStats(
            consumed=totals[0],
            succeeded=totals[1],
            failed=totals[2],
            duplicates=totals[3],
            skipped=totals[4],
        )

    # ------------------------------------------------------------ 单条

    def _handle(self, message: QueueMessage) -> PipelineOutcome:
        """处理一条消息，**无论成败都 ack**（理由见模块 docstring）。"""

        try:
            outcome = self._process(message.job_id)
        except Exception:  # noqa: BLE001 - 单条消息的任何异常都不该杀掉 worker
            logger.exception("[worker] 处理消息时出现未预期异常 job=%s", message.job_id)
            outcome = PipelineOutcome(
                job_id=message.job_id,
                tenant_id="",
                status="failed",
                error_code="unexpected_error",
                error_message="worker 处理消息时出现未预期异常（详见日志）",
            )
        finally:
            self._ack(message)
        return outcome

    def _process(self, job_id: str) -> PipelineOutcome:
        with self._session_scope() as session:
            job = repository.get_ingestion_job_unscoped(session, job_id)
            if job is None:
                logger.warning("[worker] 消息指向的任务不存在（已删除或非本库）job=%s", job_id)
                return PipelineOutcome(
                    job_id=job_id,
                    tenant_id="",
                    skipped=True,
                    skip_reason="job_not_found",
                )
            tenant_id = job.tenant_id

        # 流水线自己开事务（阶段结束即提交），因此这里给它一个独立会话
        with self._session_scope() as session:
            return process_job(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                store=self.store,
                **self.pipeline_options,
            )

    def _ack(self, message: QueueMessage) -> None:
        try:
            self.queue.ack(message.message_id)
        except QueueUnavailableError as error:  # pragma: no cover - 需要 Redis 故障才能触发
            logger.error("[worker] ack 失败（消息可能被重复投递）message=%s：%s", message.message_id, error)

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _install_signal_handlers(stop: dict[str, bool]) -> None:
        """SIGINT / SIGTERM 只置停止标志：正在处理的任务跑完再退出。

        直接退出会让"正在写 chunk 的任务"停在半路 —— 虽然流水线每个阶段都幂等、
        下次重跑能收敛，但让正在跑的一轮跑完是更好的默认行为
        （否则一个 Ctrl-C 就会让一堆任务变成需要人工 reprocess 的 failed）。
        """

        def _stop(_signum, _frame):  # pragma: no cover - 信号路径靠人工验证
            logger.info("[worker] 收到停止信号，等待当前任务结束")
            stop["flag"] = True

        for name in ("SIGINT", "SIGTERM"):
            handler = getattr(signal, name, None)
            if handler is not None:
                try:
                    signal.signal(handler, _stop)
                except (ValueError, OSError):  # pragma: no cover - 非主线程
                    pass


def build_worker(
    *,
    queue: IngestionQueue | None = None,
    session_factory: sessionmaker[Session] | None = None,
    store: ObjectStore | None = None,
    consumer_name: str = "worker-1",
    batch: int = 4,
    index_root: str | Path | None = None,
    embedder: Callable[[str], Any] | None = None,
) -> IngestionWorker:
    """按环境变量装配一个 worker（供 CLI 使用，测试直接构造类）。"""

    options: dict[str, Any] = {}
    if index_root is not None:
        options["index_root"] = index_root
    if embedder is not None:
        options["embedder"] = embedder
    return IngestionWorker(
        queue=queue or get_queue(),
        session_factory=session_factory,
        store=store,
        consumer_name=consumer_name,
        batch=batch,
        pipeline_options=options,
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：``python -m services.ingestion.worker [--once] [--batch N]``。

    启动时打印一次生效的切分配置（``CHUNK_CONFIG``）——因为非法配置会让 import
    期直接失败，能走到这里说明配置合法，把摘要打出来便于核对"线上到底用的哪套参数"。
    """

    import argparse

    parser = argparse.ArgumentParser(description="RAG 接入任务消费者")
    parser.add_argument("--once", action="store_true", help="只消费一轮就退出（联调用）")
    parser.add_argument("--batch", type=int, default=4, help="每轮最多取多少条消息")
    parser.add_argument("--consumer-name", default="worker-1", help="消费者名（写进 Redis pending 列表）")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="空轮轮询间隔（秒）")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    logger.info("[worker] 启动 consumer=%s chunk_config=%s", args.consumer_name, CHUNK_CONFIG.summary())

    worker = build_worker(consumer_name=args.consumer_name, batch=args.batch)
    if args.once:
        outcomes = worker.run_once()
        for outcome in outcomes:
            logger.info("[worker] outcome=%s", outcome.to_dict())
        return 0 if not any(item.failed for item in outcomes) else 1

    stats = worker.run_forever(poll_interval=args.poll_interval, install_signal_handlers=True)
    logger.info("[worker] 退出 stats=%s", stats.to_dict())
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口自身不测
    raise SystemExit(main())


__all__ = [
    "IngestionWorker",
    "WorkerStats",
    "build_worker",
    "main",
]
