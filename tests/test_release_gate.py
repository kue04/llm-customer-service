"""B8 发布门禁：四种文件格式的端到端链路（阶段 7.2 判据）。

这是**唯一一条从 HTTP 入口走到检索结果**的测试
------------------------------------------------
其它测试都是分段的：`test_document_upload_api` 测到"入库建了 job"，
`test_ingestion_pipeline` 测到"流水线跑完 8 阶段"，
`test_retrieval_api` 测到"索引能按权限过滤检索"。
**但没有任何一条把它们串起来** —— 而 7.2 要的正是"串起来能跑通"：
上传 → 解析 → 切分 → 索引 → 检索得到。

这也是 F1「双轨制」的教训：两段各自全绿，接口对上才暴露问题
（B14 的静默零命中就是这么发现的）。所以本文件**刻意走真实路由**
（`POST /knowledge-bases/{id}/documents` 与 `POST /retrieval/search`），
而不是绕过 HTTP 直接调 pipeline。

覆盖 7.2 的八条
---------------
1. 走**真实路由**上传 → 拿 `document_id` / `job_id`；
2. 消费任务（测试内直接 `process_job`，等价于 worker）→ 跑完流水线；
3. 走**真实端点**重建索引（`POST /ingestion/indexes/rebuild`）；
4. `POST /retrieval/search` 检索，断言**命中本文档的 chunk**；
5. 链路串联：命中的 `document_id` / `document_version_id` / `tenant_id` 一致，
   且 `text` 与库里的 chunk 逐字相同（索引里的东西就是库里的东西）；
6. 长文档至少 2 个 chunk（Markdown 样本）；
7. 重复上传同一文件 → **不产生第二份有效版本**（content_hash 判重）；
8. 损坏文件 → 失败**可见**（`error_code`）且**可重试**（`/reprocess`）。

关于「检索 query 怎么选」
-------------------------
测试用的 embedder 是 `fake_embedding`（词袋哈希：同词共现 → 相似度高）。
query 因此**取自待检索文档自己的 chunk 文本** —— 等价于"用户问文档里写过的内容"。
这是刻意的：本文件要验证的是**链路通不通**，不是检索质量。
检索质量属于 M10 Recall@k，而它的口径修正尚未做（见验收规范 §10.3 的 N/A 判据）。
"""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from auth_helpers import auth_headers
from retrieval_fixtures import EMBEDDING_MODEL, alembic_upgrade, fake_embedding
from routers import documents as documents_router
from routers import retrieval
from services.ingestion import db, object_store, queue as queue_module, repository
from services.ingestion.object_store import LocalObjectStore
from services.ingestion.pipeline import IngestionPipeline


FIXTURES = Path(__file__).resolve().parent / "fixtures"

TENANT = "tenant-release-gate"
UPLOADER = "gate-uploader"
REQUEST_ID = "req-release-gate"

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: 四种格式的样本（由 `scripts/make_parser_fixtures.py` 生成，可复现）
FORMATS = (
    pytest.param("pdf", "sample.pdf", "application/pdf", id="pdf"),
    pytest.param("docx", "sample.docx", DOCX_CONTENT_TYPE, id="docx"),
    pytest.param("html", "sample.html", "text/html", id="html"),
    pytest.param("md", "sample.md", "text/markdown", id="md"),
)


# ---------------------------------------------------------------- 测试环境


@dataclass
class Gate:
    """一组端到端夹具：真实路由 + 确定性的 embedding 替身。"""

    client: TestClient
    db_url: str
    index_root: Path
    store: LocalObjectStore
    kb_id: str

    def headers(self) -> dict[str, str]:
        headers = auth_headers(tenant_id=TENANT, user_id=UPLOADER, roles=["admin"])
        headers["X-Request-Id"] = REQUEST_ID
        return headers

    def upload(self, filename: str, data: bytes, content_type: str):
        """走**真实上传路由**（不是直接调 pipeline）。"""

        return self.client.post(
            f"/knowledge-bases/{self.kb_id}/documents",
            files={"file": (filename, data, content_type)},
            headers=self.headers(),
        )

    def process(self, job_id: str):
        """消费任务，等价于 worker 的消费端（7.2 第 2 条允许测试内直接调用）。"""

        with db.session_scope(self.db_url) as session:
            return IngestionPipeline(
                session=session,
                store=self.store,
                embedder=fake_embedding,
                embedding_model=EMBEDDING_MODEL,
                index_root=self.index_root,
            ).process_job(tenant_id=TENANT, job_id=job_id)

    def rebuild(self):
        """走**真实端点**重建索引。"""

        return self.client.post("/ingestion/indexes/rebuild", headers=self.headers())

    def search(self, query: str, *, limit: int = 10):
        return self.client.post(
            "/retrieval/search",
            headers=self.headers(),
            json={"query": query, "limit": limit},
        )

    def chunks_of(self, document_id: str) -> list:
        with db.session_scope(self.db_url) as session:
            version = repository.latest_document_version(session, TENANT, document_id)
            assert version is not None, "流水线跑完却没有文档版本"
            return list(repository.list_chunks(session, TENANT, version.id)), version


@pytest.fixture()
def gate(tmp_path, monkeypatch) -> Gate:
    """临时 SQLite + 本地对象存储 + 内存队列；**两组生产默认值**都换成测试替身。

    「两组」指上传路由与检索路由各读一组（`default_index_root` / `default_embedder` /
    `default_embedding_model`）。**必须换成同一个 embedder** ——
    否则查询向量与索引向量不在同一空间，分数没有任何意义。
    """

    url = f"sqlite:///{(tmp_path / 'release_gate.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    monkeypatch.setenv(object_store.ROOT_ENV, str(tmp_path / "object_store"))
    # 确保不会有 Redis URL 泄漏进来改变队列选择（内存队列才是我们要的）
    monkeypatch.delenv(queue_module.STREAM_URL_ENV, raising=False)
    alembic_upgrade(url)

    index_root = tmp_path / "faiss_store"
    store = LocalObjectStore(tmp_path / "object_store")

    for module in (documents_router, retrieval):
        monkeypatch.setattr(module, "default_index_root", lambda: index_root)
        monkeypatch.setattr(module, "default_embedder", fake_embedding)
        monkeypatch.setattr(module, "default_embedding_model", lambda: EMBEDDING_MODEL)

    app = FastAPI()
    app.include_router(documents_router.router)
    app.include_router(retrieval.router, prefix="/retrieval")

    memory_queue = queue_module.InMemoryIngestionQueue()
    app.dependency_overrides[queue_module.get_queue] = lambda: memory_queue

    with db.session_scope(url) as session:
        repository.create_tenant(session, tenant_id=TENANT, name="release-gate")
        knowledge_base = repository.create_knowledge_base(session, tenant_id=TENANT, slug="kb-gate")
        user = repository.create_user(session, tenant_id=TENANT, external_id=UPLOADER)
        repository.add_knowledge_base_member(
            session,
            tenant_id=TENANT,
            knowledge_base_id=knowledge_base.id,
            user_id=user.id,
            member_role="editor",
        )

    try:
        yield Gate(
            client=TestClient(app),
            db_url=url,
            index_root=index_root,
            store=store,
            kb_id=knowledge_base.id,
        )
    finally:
        db.dispose_engines()


# ---------------------------------------------------------------- 7.2 主判据：四格式


@pytest.mark.parametrize("source_type,filename,content_type", FORMATS)
def test_four_formats_reach_the_index_and_come_back_from_search(
    gate: Gate, source_type: str, filename: str, content_type: str
) -> None:
    """上传 → 解析 → 切分 → 索引 → **检索得到**（7.2 第 1~5 条）。"""

    data = (FIXTURES / filename).read_bytes()

    # 1) 走真实路由上传
    receipt = gate.upload(filename, data, content_type)
    assert receipt.status_code == 202, receipt.text
    body = receipt.json()
    document_id, job_id = body["document_id"], body["job_id"]
    assert body["source_type"] == source_type

    # 2) 消费任务
    outcome = gate.process(job_id)
    assert outcome.succeeded, outcome.to_dict()

    # 3) 走真实端点重建索引
    rebuilt = gate.rebuild()
    assert rebuilt.status_code == 200, rebuilt.text

    chunks, version = gate.chunks_of(document_id)
    assert chunks, "流水线跑完却没有 chunk"

    # 4) 检索：query 取自本文档自己的 chunk（见模块 docstring 的说明）
    query = " ".join(chunks[0].text.split()[:4])
    response = gate.search(query)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["retrieval_path"] == "chunk-index", "检索退回了演示路径（A 轨）"
    assert payload["count"] >= 1, f"零命中：query={query!r}，索引版本={payload['index']['index_version']}"

    # 5) 链路串联：命中必须来自本文档，且索引文本与库内文本逐字一致
    own = {chunk.chunk_id: chunk for chunk in chunks}
    hits = payload["results"]
    matching = [item for item in hits if item["chunk_id"] in own]
    assert matching, f"命中里没有本文档的 chunk：{sorted(own)[:3]} vs {[i['chunk_id'] for i in hits][:3]}"

    hit = matching[0]
    assert hit["document_id"] == document_id
    assert hit["document_version_id"] == version.id
    assert hit["document_version"] == version.version
    assert hit["tenant_id"] == TENANT
    assert hit["source_type"] == source_type
    assert hit["text"] == own[hit["chunk_id"]].text, "索引里的文本与库里的 chunk 不一致"

    # 权限元数据随 chunk 进索引（7.3「ACL 不丢」的端到端侧证）
    assert isinstance(hit["acl"], list)
    assert isinstance(hit["heading_path"], list)


def test_long_document_yields_at_least_two_chunks(gate: Gate) -> None:
    """7.2 第 6 条：长文档至少要切出 2 个 chunk（否则索引只有一个点，检索证明不了链路）。"""

    data = (FIXTURES / "sample.md").read_bytes()
    body = gate.upload("sample.md", data, "text/markdown").json()
    assert gate.process(body["job_id"]).succeeded

    chunks, _version = gate.chunks_of(body["document_id"])
    assert len(chunks) >= 2, f"Markdown 样本只切出 {len(chunks)} 个 chunk"


# ---------------------------------------------------------------- 7.2 第 7 条：判重


def test_reuploading_the_same_file_does_not_add_a_second_effective_version(gate: Gate) -> None:
    """重复上传同一文件 → 不产生第二份**有效**版本（`content_hash` 判重，B6 起）。"""

    data = (FIXTURES / "sample.md").read_bytes()

    first = gate.upload("sample.md", data, "text/markdown").json()
    assert gate.process(first["job_id"]).succeeded
    document_id = first["document_id"]

    second = gate.upload("sample.md", data, "text/markdown")
    assert second.status_code == 202, second.text
    assert second.json()["document_id"] == document_id, "同一 source_uri 应复用同一份文档"
    gate.process(second.json()["job_id"])

    with db.session_scope(gate.db_url) as session:
        versions = list(repository.list_document_versions(session, TENANT, document_id))

    assert len(versions) == 1, f"重复上传产生了第二份有效版本：{[v.id for v in versions]}"


# ---------------------------------------------------------------- 7.2 第 8 条：失败可见且可重试


def test_broken_file_fails_visibly_and_can_be_reprocessed(gate: Gate) -> None:
    """损坏文件：失败要**看得见**（`error_code`），并且**能重试**（`/reprocess`）。

    「失败可见」是这条的要害：静默失败的入库会让人以为"文件已经进去了"，
    而检索永远查不到 —— 那正是 B9（OCR 缺失降级为警告而不是失败）要区分的那类判断：
    **降级的后果是"能力变弱"还是"承诺变假"**。解析失败属于后者，必须失败。
    """

    # 造一份「**能过准入、但解析必失败**」的 docx —— 这两件事要分开满足：
    #   * 准入只要求「合法 zip + 存在 word/ 条目」（`content_sniff._is_docx_zip`），
    #     所以随手拼一个 PK 头是过不去的（实测 415）；
    #   * python-docx 打开时要真的解析 `word/document.xml`，给它一段非法 XML 就够了。
    # 之前想当然地拿 `b"PK\x03\x04" + 零字节` 当"损坏文件"，被准入拦下 —— 说明
    # **准入校验和解析校验是两层**，"坏文件"要针对具体那一层来构造。
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", b"\x00\x01\x02 not xml at all")
    broken = buffer.getvalue()

    receipt = gate.upload("broken.docx", broken, DOCX_CONTENT_TYPE)
    assert receipt.status_code == 202, receipt.text
    body = receipt.json()

    outcome = gate.process(body["job_id"])
    assert not outcome.succeeded, "损坏文件不该被判为成功"

    job = gate.client.get(f"/ingestion-jobs/{body['job_id']}", headers=gate.headers())
    assert job.status_code == 200, job.text
    detail = job.json()
    assert detail["error_code"], "失败任务必须带 error_code（否则用户只会看到'处理中'）"

    retry = gate.client.post(f"/documents/{body['document_id']}/reprocess", headers=gate.headers())
    assert retry.status_code in (200, 201, 202), retry.text
    assert retry.json()["job_id"] != body["job_id"], "重试应当新建一个 job，而不是复用失败的那个"
