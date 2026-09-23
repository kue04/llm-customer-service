"""阶段 2.3 上传与异步任务 API 的测试（批次 B4）。

覆盖范围（对应计划 2.3 与对接文档第 8 节的验收清单）
----------------------------------------------------
1. **准入**：扩展名白名单与 ``parser_registry.ALLOWED_EXTENSIONS`` 同源；
   内容与扩展名不一致（magic bytes）拒绝；超大文件 413；空文件名 415；
2. **鉴权与隔离**：无令牌 401；缺权限 403；知识库成员角色按
   ``KB_MEMBER_ROLES × KB_WRITE_MEMBER_ROLES`` 的**完整真值表**校验
   （owner / editor 放行，reviewer / viewer 拒绝，两个集合本身另有守卫断言）；
   知识库/文档/任务跨租户一律 404 且与「不存在」响应逐字节相同（不泄漏存在性）；
   ``tenant_id`` 只来自令牌，查询串里的同名字段被忽略；
3. **上传语义**：只建 ``documents``（按需）+ ``ingestion_jobs``，
   **不建 ``document_versions``**（决策 [D-6]）；job 初值 ``pending`` / ``received``；
   同名重传复用同一份文档并各建一个 job；对象 key 含租户路径与文档 ID；
4. **审计**：写操作落 ``audit_events``，含 actor / tenant / action / resource /
   request_id 与脱敏摘要；
5. **任务查询**：失败任务可查询（``error_code`` / ``error_message``）、
   解析警告从 ``document_versions.metadata_json["warnings"]`` 读出并展开、
   失败后可通过 ``/reprocess`` 新建 job 重试；
6. **基础设施单元**：``ObjectStore``（含路径逃逸防护）、内容嗅探、
   队列（内存降级 + 注入假客户端的 Redis 分支 + 缺客户端时报不可用）。

测试跑在临时 SQLite 上：schema 由 ``models.Base.metadata.create_all`` 建出
（「迁移产物与模型定义逐项一致」由 ``tests/test_ingestion_models.py`` 单独守着，
这里不必重复跑迁移，省下的时间用来覆盖行为）。
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import io
import sys
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from auth_helpers import auth_headers
from routers import documents as documents_router
from services.ingestion import (
    content_sniff,
    db,
    models,
    object_store,
    parser_registry,
    queue as queue_module,
    repository,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"

TENANT_A = "tenant-upload-a"
TENANT_B = "tenant-upload-b"
UPLOADER = "uploader-1"
REQUEST_ID = "req-upload-1"

MD_PAYLOAD = "# 客户服务手册\n\n正文段落。\n".encode("utf-8")
HTML_PAYLOAD = b"<!DOCTYPE html>\n<html><body><h1>title</h1><p>body</p></body></html>"
TXT_PAYLOAD = "中文文本，没有任何魔数可以识别。\n".encode("utf-8")
PDF_PAYLOAD = (FIXTURES / "sample.pdf").read_bytes()
DOCX_PAYLOAD = (FIXTURES / "sample.docx").read_bytes()

#: 与扩展名一致的内容样本，用来遍历 10 个允许的扩展名
PAYLOAD_BY_EXTENSION = {
    ".md": MD_PAYLOAD,
    ".markdown": MD_PAYLOAD,
    ".mdown": MD_PAYLOAD,
    ".html": HTML_PAYLOAD,
    ".htm": HTML_PAYLOAD,
    ".xhtml": HTML_PAYLOAD,
    ".txt": TXT_PAYLOAD,
    ".text": TXT_PAYLOAD,
    ".pdf": PDF_PAYLOAD,
    ".docx": DOCX_PAYLOAD,
}


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(documents_router.router, tags=["documents"])
    return app


def _seed_knowledge_base(
    session,
    *,
    tenant_id: str,
    slug: str,
    member_role: str | None = "editor",
    user_external_id: str = UPLOADER,
) -> str:
    knowledge_base = repository.create_knowledge_base(session, tenant_id=tenant_id, slug=slug)
    if member_role is not None:
        user = repository.get_user_by_external_id(session, tenant_id, user_external_id)
        if user is None:
            user = repository.create_user(session, tenant_id=tenant_id, external_id=user_external_id)
        repository.add_knowledge_base_member(
            session,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base.id,
            user_id=user.id,
            member_role=member_role,
        )
    return knowledge_base.id


@dataclass
class Api:
    """测试用的一组夹具与便捷调用。"""

    client: TestClient
    db_url: str
    store_root: Path
    queue: queue_module.InMemoryIngestionQueue
    kb_ids: dict[str, str]

    def token_headers(
        self,
        *,
        roles: tuple[str, ...] = ("supervisor",),
        user_id: str = UPLOADER,
        tenant_id: str = TENANT_A,
        request_id: str = REQUEST_ID,
    ) -> dict[str, str]:
        headers = auth_headers(roles=list(roles), user_id=user_id, tenant_id=tenant_id)
        headers["X-Request-Id"] = request_id
        return headers

    def upload(
        self,
        *,
        filename: str = "sample.md",
        data: bytes = MD_PAYLOAD,
        content_type: str = "text/markdown",
        kb_key: str = "main",
        headers: dict[str, str] | None = None,
        kb_id: str | None = None,
        **header_kwargs,
    ):
        knowledge_base_id = kb_id or self.kb_ids[kb_key]
        return self.client.post(
            f"/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": (filename, data, content_type)},
            headers=self.token_headers(**header_kwargs) if headers is None else headers,
        )

    def seed_document(self, *, name: str = "sample.md", tenant_id: str = TENANT_A, kb_key: str = "main") -> tuple[str, str]:
        """直接落一份文档 + 一个 job，供读接口测试使用（不经过上传接口）。"""

        with db.session_scope(self.db_url) as session:
            document = repository.create_document(
                session,
                tenant_id=tenant_id,
                knowledge_base_id=self.kb_ids[kb_key],
                source_uri=object_store.logical_source_uri(name),
                source_type="md",
                title=Path(name).stem,
            )
            job = repository.create_ingestion_job(
                session, tenant_id=tenant_id, document_id=document.id, status="pending", stage="received"
            )
            return document.id, job.id


@pytest.fixture()
def api(tmp_path, monkeypatch) -> Api:
    url = f"sqlite:///{(tmp_path / 'rag_upload.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    store_root = tmp_path / "object_store"
    monkeypatch.setenv(object_store.ROOT_ENV, str(store_root))
    # 确保不会有 Redis URL 泄漏进来影响队列选择
    monkeypatch.delenv(queue_module.STREAM_URL_ENV, raising=False)

    models.Base.metadata.create_all(db.get_engine(url))

    with db.session_scope(url) as session:
        repository.create_tenant(session, name="tenant-a", tenant_id=TENANT_A)
        repository.create_tenant(session, name="tenant-b", tenant_id=TENANT_B)
        kb_ids = {
            "main": _seed_knowledge_base(session, tenant_id=TENANT_A, slug="kb-main"),
            "second": _seed_knowledge_base(session, tenant_id=TENANT_A, slug="kb-second"),
            "viewer": _seed_knowledge_base(session, tenant_id=TENANT_A, slug="kb-viewer", member_role="viewer"),
            "foreign": _seed_knowledge_base(session, tenant_id=TENANT_A, slug="kb-foreign", member_role=None),
            "tenant_b": _seed_knowledge_base(session, tenant_id=TENANT_B, slug="kb-b"),
        }

    memory_queue = queue_module.InMemoryIngestionQueue()
    app = build_app()
    # 队列走依赖覆盖：既能直接观测投递内容，又不依赖任何真实 Redis
    app.dependency_overrides[queue_module.get_queue] = lambda: memory_queue

    yield Api(
        client=TestClient(app),
        db_url=url,
        store_root=store_root,
        queue=memory_queue,
        kb_ids=kb_ids,
    )
    db.dispose_engines()


def stored_object_bytes(api: Api, *, document_id: str, filename: str = "sample.md", tenant_id: str = TENANT_A) -> bytes:
    key = object_store.build_object_key(tenant_id, document_id, filename)
    return (api.store_root / key).read_bytes()


# ════════════════════════════════════════════════════════════ 上传：成功路径


def test_upload_returns_202_with_document_and_job(api: Api):
    response = api.upload()
    assert response.status_code == 202, response.text

    body = response.json()
    assert body["status"] == "accepted"
    assert body["job_status"] == "pending"
    assert body["job_stage"] == "received"
    assert body["source_type"] == "md"
    assert body["filename"] == "sample.md"
    assert body["size_bytes"] == len(MD_PAYLOAD)
    assert body["source_uri"] == "upload://sample.md"
    assert body["reused_document"] is False
    assert body["knowledge_base_id"] == api.kb_ids["main"]
    assert body["document_id"] and body["job_id"]


def test_upload_persists_document_job_and_object_bytes(api: Api):
    body = api.upload().json()
    document_id, job_id = body["document_id"], body["job_id"]

    with db.session_scope(api.db_url) as session:
        document = repository.get_document(session, TENANT_A, document_id)
        assert document is not None
        assert document.status == "received"  # 上传期不是 stored（stored 由流水线推进）
        assert document.source_type == "md"
        assert document.title == "sample"  # 暂用文件名主干，解析后由流水线覆盖
        assert document.knowledge_base_id == api.kb_ids["main"]

        job = repository.get_ingestion_job(session, TENANT_A, job_id)
        assert job is not None
        assert (job.status, job.stage) == ("pending", "received")
        assert job.document_version_id is None

        # [D-6]：上传期不建版本，版本由流水线在 parsed 阶段创建
        assert list(repository.list_document_versions(session, TENANT_A, document_id)) == []
        assert session.query(models.DocumentVersion).count() == 0

    # 对象存储里必须是**原始字节**（解析器直接吃 bytes），路径含租户与文档 ID
    assert stored_object_bytes(api, document_id=document_id) == MD_PAYLOAD
    stored_path = api.store_root / object_store.build_object_key(TENANT_A, document_id, "sample.md")
    assert stored_path.is_file()
    assert stored_path.parts[-2] == document_id
    assert TENANT_A in stored_path.parts


def test_upload_enqueues_job(api: Api):
    body = api.upload().json()
    assert api.queue.depth() == 1
    assert api.queue.pending_job_ids() == [body["job_id"]]


def test_upload_writes_audit_event_with_masked_summary(api: Api):
    response = api.upload(filename="13800138000.txt", data=TXT_PAYLOAD, content_type="text/plain")
    assert response.status_code == 202
    document_id = response.json()["document_id"]

    with db.session_scope(api.db_url) as session:
        events = repository.list_audit_events(session, TENANT_A, resource_type="document", resource_id=document_id)
        assert len(events) == 1
        event = events[0]
        assert event.action == "document_upload"
        assert event.actor_id == UPLOADER
        assert event.request_id == REQUEST_ID
        assert event.summary_json["size_bytes"] == len(TXT_PAYLOAD)
        # 结构化 ID 保持原样，否则审计摘要里的 ID 会被通用数字打码规则打碎、无法关联
        assert event.summary_json["knowledge_base_id"] == api.kb_ids["main"]
        assert event.summary_json["job_id"] == response.json()["job_id"]
        assert event.summary_json["reused_document"] is False
        assert event.summary_json["actor_role"] == "supervisor"
        # 自由文本必须脱敏：文件名里的手机号不能原样落库
        assert event.summary_json["filename"] == "[手机号已脱敏].txt"


@pytest.mark.parametrize("extension", sorted(PAYLOAD_BY_EXTENSION))
def test_upload_accepts_every_extension_from_registry(api: Api, extension: str):
    """白名单与 `parser_registry.ALLOWED_EXTENSIONS` 同源，10 个扩展名全部可用。"""

    assert extension in parser_registry.ALLOWED_EXTENSIONS
    payload = PAYLOAD_BY_EXTENSION[extension]
    response = api.upload(filename=f"policy{extension}", data=payload)
    assert response.status_code == 202, response.text


def test_extension_samples_cover_the_whole_registry_whitelist():
    """参数化用例必须覆盖白名单全集，否则「10 个扩展名都可用」是一句没有证据的话。"""

    assert len(parser_registry.ALLOWED_EXTENSIONS) == 10
    assert set(PAYLOAD_BY_EXTENSION) == set(parser_registry.ALLOWED_EXTENSIONS)


def test_uploaded_file_without_magic_signature_is_still_accepted(api: Api):
    """txt / md / html 没有魔数，一律放行 —— 否则正常中文文档会被误杀。"""

    response = api.upload(filename="说明.md", data="中文说明，无任何文件头特征。".encode("utf-8"))
    assert response.status_code == 202


def test_markdown_starting_with_html_is_still_accepted(api: Api):
    """Markdown 内嵌 HTML 是常见写法，弱特征格式不做内容准入。"""

    response = api.upload(filename="note.md", data=b"<!DOCTYPE html>\n<p>embedded</p>" + MD_PAYLOAD)
    assert response.status_code == 202


def test_upload_accepts_real_pdf_even_with_wrong_declared_mime(api: Api):
    """B3 的「扩展名优先于 MIME」在新接口上必须继续成立。

    声明成 text/html 但内容是合法 PDF：按扩展名走 PDF 路径并放行 ——
    否则攻击者可以用伪造的 Content-Type 把文件带偏到别的解析器。
    """

    response = api.upload(filename="report.pdf", data=PDF_PAYLOAD, content_type="text/html")
    assert response.status_code == 202
    assert response.json()["source_type"] == "pdf"


def test_rejected_upload_writes_nothing_to_object_store(api: Api):
    """准入检查必须发生在对象存储写入之前，否则拒绝会留下孤儿字节。"""

    assert api.upload(filename="report.pdf", data=HTML_PAYLOAD).status_code == 415
    assert list(api.store_root.rglob("*")) == []
    with db.session_scope(api.db_url) as session:
        assert session.query(models.Document).count() == 0
        assert session.query(models.IngestionJob).count() == 0


def test_router_delegates_parsing_and_never_hashes_content():
    """上传接口不解析、不自选解析器、不算内容 hash（与 [D-5] 同类风险，用测试兜住）。

    用 AST 检查**代码**而不是源码字符串：模块 docstring 里必须能自由提到
    ``parse_document()`` 说明职责边界，不该因此被这条守卫误伤。
    """

    source = Path(documents_router.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    package_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
            if node.module == "services.ingestion":
                package_names.update(alias.name for alias in node.names)

    # 只允许依赖解析契约里的错误类型；具体解析器与解析入口都不许 import
    parser_imports = {module for module in imported_modules if module.startswith("services.ingestion.parsers")}
    assert parser_imports == {"services.ingestion.parsers.base"}, parser_imports
    assert "parser_registry" in package_names
    assert "hashlib" not in imported_modules

    called_names = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "parse_document" not in called_names

    # 白名单同源：直接引用注册表常量，不在 router 里维护第二份扩展名列表
    assert "parser_registry.ALLOWED_EXTENSIONS" in source
    assert '".pdf"' not in source


# ════════════════════════════════════════════════════════════ 上传：拒绝路径


@pytest.mark.parametrize("filename", ["malware.exe", "archive.zip", "noextension", "report.pdf.exe", "script.sh"])
def test_upload_rejects_unsupported_extension(api: Api, filename: str):
    response = api.upload(filename=filename, data=PDF_PAYLOAD)
    assert response.status_code == 415
    assert "不支持的文件类型" in response.json()["detail"]


def test_upload_rejects_file_with_mismatched_content(api: Api):
    """扩展名说 pdf、内容却是 HTML —— 计划 2.3 明确要求的「MIME 与内容不一致」。"""

    response = api.upload(filename="report.pdf", data=HTML_PAYLOAD, content_type="application/pdf")
    assert response.status_code == 415
    assert "不一致" in response.json()["detail"]


def test_upload_rejects_plain_text_disguised_as_pdf(api: Api):
    response = api.upload(filename="report.pdf", data=TXT_PAYLOAD, content_type="application/pdf")
    assert response.status_code == 415


def test_upload_rejects_docx_which_is_not_a_zip(api: Api):
    response = api.upload(filename="report.docx", data=b"%PDF-1.7 not a docx", content_type="application/pdf")
    assert response.status_code == 415


def test_upload_rejects_zip_without_word_members_disguised_as_docx(api: Api):
    buffer = _zip_bytes({"xl/workbook.xml": b"<workbook/>"})
    response = api.upload(filename="sheet.docx", data=buffer, content_type="application/octet-stream")
    assert response.status_code == 415


def test_upload_rejects_oversized_file(api: Api, monkeypatch):
    monkeypatch.setenv(documents_router.MAX_UPLOAD_MB_ENV, "1")
    response = api.upload(filename="big.txt", data=b"a" * (1024 * 1024 + 1), content_type="text/plain")
    assert response.status_code == 413
    assert api.queue.depth() == 0


def test_upload_accepts_file_exactly_at_limit(api: Api, monkeypatch):
    monkeypatch.setenv(documents_router.MAX_UPLOAD_MB_ENV, "1")
    response = api.upload(filename="exact.txt", data=b"a" * (1024 * 1024), content_type="text/plain")
    assert response.status_code == 202


def test_default_upload_limit_is_50mb(api: Api, monkeypatch):
    monkeypatch.delenv(documents_router.MAX_UPLOAD_MB_ENV, raising=False)
    assert documents_router.DEFAULT_MAX_UPLOAD_MB == 50
    assert documents_router._max_upload_bytes() == 50 * 1024 * 1024


@pytest.mark.parametrize("value", ["abc", "0", "-3"])
def test_invalid_upload_limit_configuration_fails_loud(api: Api, monkeypatch, value: str):
    """配置写错时报 500 而不是静默用默认值 —— 否则「限制没生效」不可观测。"""

    monkeypatch.setenv(documents_router.MAX_UPLOAD_MB_ENV, value)
    response = api.upload()
    assert response.status_code == 500
    assert documents_router.MAX_UPLOAD_MB_ENV in response.json()["detail"]


# ════════════════════════════════════════════════════════════ 鉴权与租户隔离


def test_upload_requires_token(api: Api):
    response = api.upload(headers={})
    assert response.status_code == 401


def test_upload_requires_write_role(api: Api):
    response = api.upload(roles=("agent",))
    assert response.status_code == 403


def test_upload_requires_knowledge_base_membership(api: Api):
    response = api.upload(kb_key="foreign")
    assert response.status_code == 403


def test_upload_rejects_read_only_member(api: Api):
    response = api.upload(kb_key="viewer")
    assert response.status_code == 403


def test_upload_allows_knowledge_base_owner(api: Api):
    """owner 是写白名单的另一半。

    ``KB_WRITE_MEMBER_ROLES`` 有 owner / editor 两项，若只测 editor 放行 +
    viewer 拒绝，实现里把 owner 漏掉（写成 ``{"editor"}``）时测试不会红 ——
    owner 是知识库创建者的默认角色，漏掉等于「库主自己传不上文件」。
    """

    with db.session_scope(api.db_url) as session:
        kb_id = _seed_knowledge_base(session, tenant_id=TENANT_A, slug="kb-owner", member_role="owner")

    response = api.upload(kb_id=kb_id)
    assert response.status_code == 202, response.text


def test_upload_rejects_reviewer_member(api: Api):
    """reviewer 带「审核」语义，最容易被误认为可写；实际上它不在写白名单里。

    注意它**合法**（``KB_MEMBER_ROLES`` 收录），所以拒绝必须发生在
    「角色 ∉ 写白名单」这一步，而不是成员关系不存在那一步。
    """

    with db.session_scope(api.db_url) as session:
        kb_id = _seed_knowledge_base(session, tenant_id=TENANT_A, slug="kb-reviewer", member_role="reviewer")

    response = api.upload(kb_id=kb_id)
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("member_role", models.KB_MEMBER_ROLES)
def test_upload_respects_knowledge_base_role_matrix(api: Api, member_role: str):
    """上传准入必须与 ``KB_MEMBER_ROLES × KB_WRITE_MEMBER_ROLES`` 的笛卡尔积一致。

    这条用**两张真值表**驱动而不是抽样个例：以后往 ``KB_MEMBER_ROLES`` 里加角色时，
    要么被写白名单接纳（202）、要么被拒（403），二者必居其一 —— 不会出现
    「新角色没被授权、也没有测试提醒」的静默缺口。
    """

    with db.session_scope(api.db_url) as session:
        kb_id = _seed_knowledge_base(
            session, tenant_id=TENANT_A, slug=f"kb-role-{member_role}", member_role=member_role
        )

    response = api.upload(kb_id=kb_id)
    if member_role in documents_router.KB_WRITE_MEMBER_ROLES:
        assert response.status_code == 202, response.text
    else:
        assert response.status_code == 403, response.text


def test_write_member_roles_are_a_strict_subset_of_all_member_roles():
    """写白名单必须是合法角色的**真子集**（纯集合断言，不建库）。

    两个方向都要守：
    * 子集：把角色名拼错（如 ``"ower"``）会让白名单永远不匹配 —— 且是静默的
      「加了成员也永远 403」，不会报错；
    * 真子集：若写白名单等于全集，说明「成员分级」根本没生效（viewer 也能写）。
    """

    all_roles = set(models.KB_MEMBER_ROLES)
    write_roles = documents_router.KB_WRITE_MEMBER_ROLES

    assert write_roles, "写白名单不能为空"
    assert write_roles <= all_roles, f"写白名单含未登记角色：{sorted(write_roles - all_roles)}"
    assert write_roles < all_roles, "写白名单等于全部角色 = 成员分级失效"


def test_upload_to_other_tenant_knowledge_base_is_indistinguishable_from_missing(api: Api):
    cross_tenant = api.upload(kb_key="tenant_b")
    missing = api.upload(kb_id="0" * 32)
    assert cross_tenant.status_code == 404
    assert missing.status_code == 404
    # 跨租户与不存在必须逐字节相同，否则可以用状态码/消息差异探测知识库是否存在
    assert cross_tenant.json() == missing.json()


def test_tenant_id_in_query_string_is_ignored(api: Api):
    """tenant 只能来自令牌：查询串里塞别的租户不改变落库归属。"""

    headers = api.token_headers()
    response = api.client.post(
        f"/knowledge-bases/{api.kb_ids['main']}/documents?tenant_id={TENANT_B}",
        files={"file": ("sample.md", MD_PAYLOAD, "text/markdown")},
        headers=headers,
    )
    assert response.status_code == 202
    with db.session_scope(api.db_url) as session:
        document = repository.get_document(session, TENANT_A, response.json()["document_id"])
        assert document is not None and document.tenant_id == TENANT_A
        assert repository.get_document(session, TENANT_B, response.json()["document_id"]) is None


def test_same_filename_in_two_tenants_creates_independent_documents(api: Api):
    first = api.upload()
    second = api.upload(kb_key="tenant_b", tenant_id=TENANT_B)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["document_id"] != second.json()["document_id"]


# ════════════════════════════════════════════════════════════ 重传与版本归属


def test_reupload_same_filename_reuses_document_and_adds_job(api: Api):
    first = api.upload().json()
    second_response = api.upload(data=MD_PAYLOAD + b"new revision\n")
    assert second_response.status_code == 202
    second = second_response.json()

    assert second["document_id"] == first["document_id"]
    assert second["reused_document"] is True
    assert second["job_id"] != first["job_id"]

    with db.session_scope(api.db_url) as session:
        assert session.query(models.Document).count() == 1
        assert session.query(models.IngestionJob).count() == 2
        # 即便重传，上传期依然不建版本
        assert session.query(models.DocumentVersion).count() == 0

    # 每次上传都是独立的对象写入（同一 key 覆盖为最新修订）
    assert stored_object_bytes(api, document_id=first["document_id"]) == MD_PAYLOAD + b"new revision\n"
    assert api.queue.pending_job_ids() == [first["job_id"], second["job_id"]]


def test_reupload_same_filename_into_other_knowledge_base_requires_membership_there(api: Api):
    """同名文件在租户内只有一份文档，跨库复用需要**那个库**的写权限。"""

    first = api.upload().json()

    # 上传者同时是 kb-second 的成员：复用 kb-main 的文档
    reused = api.upload(kb_key="second")
    assert reused.status_code == 202
    assert reused.json()["document_id"] == first["document_id"]
    assert reused.json()["knowledge_base_id"] == api.kb_ids["main"]

    # 上传者不是 kb-foreign 的成员：禁止借同名文件改写别的库的内容
    denied = api.upload(kb_key="foreign", filename="sample.md")
    assert denied.status_code == 403


def test_document_ownership_is_not_silently_switched(api: Api):
    """复用已有文档时，文档仍归属原知识库（不会被目标库「抢走」）。"""

    first = api.upload().json()
    api.upload(kb_key="second")
    with db.session_scope(api.db_url) as session:
        document = repository.get_document(session, TENANT_A, first["document_id"])
        assert document.knowledge_base_id == api.kb_ids["main"]


# ════════════════════════════════════════════════════════════ 文档详情


def test_get_document_returns_latest_version_summary(api: Api):
    document_id, _job_id = api.seed_document()

    without_version = api.client.get(f"/documents/{document_id}", headers=api.token_headers())
    assert without_version.status_code == 200
    assert without_version.json()["latest_version"] is None

    with db.session_scope(api.db_url) as session:
        repository.create_document_version(
            session,
            tenant_id=TENANT_A,
            document_id=document_id,
            content_hash="a" * 64,
            parser_name="markdown",
            parser_version="1.0",
            metadata_json={"warnings": [{"code": "empty_section", "message": "空章节", "detail": None}]},
        )

    response = api.client.get(f"/documents/{document_id}", headers=api.token_headers())
    body = response.json()
    assert body["latest_version"]["version"] == 1
    assert body["latest_version"]["parser_name"] == "markdown"
    assert body["latest_version"]["status"] == "pending"
    # metadata 不整体返回
    assert "metadata_json" not in body["latest_version"]


def test_get_document_cross_tenant_is_indistinguishable_from_missing(api: Api):
    tenant_b_document_id, _ = api.seed_document(tenant_id=TENANT_B, kb_key="tenant_b")
    cross_tenant = api.client.get(f"/documents/{tenant_b_document_id}", headers=api.token_headers())
    missing = api.client.get("/documents/" + "0" * 32, headers=api.token_headers())
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == missing.json()


def test_get_document_requires_read_role(api: Api):
    document_id, _ = api.seed_document()
    response = api.client.get(f"/documents/{document_id}", headers=api.token_headers(roles=("agent",)))
    assert response.status_code == 403


# ════════════════════════════════════════════════════════════ 版本列表


def test_list_versions_returns_versions_in_order(api: Api):
    document_id, _ = api.seed_document()
    with db.session_scope(api.db_url) as session:
        for revision in ("r1", "r2", "r3"):
            repository.create_document_version(
                session,
                tenant_id=TENANT_A,
                document_id=document_id,
                content_hash=revision * 32,
                parser_name="plain_text",
                parser_version="1.0",
                metadata_json={"warnings": []},
            )

    response = api.client.get(f"/documents/{document_id}/versions", headers=api.token_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["version"] for item in body["items"]] == [1, 2, 3]
    assert [item["content_hash"][:2] for item in body["items"]] == ["r1", "r2", "r3"]
    assert all("metadata_json" not in item for item in body["items"])


def test_list_versions_is_empty_for_new_document(api: Api):
    body = api.upload().json()
    response = api.client.get(f"/documents/{body['document_id']}/versions", headers=api.token_headers())
    assert response.status_code == 200
    assert response.json() == {"document_id": body["document_id"], "total": 0, "items": []}


def test_list_versions_cross_tenant_is_indistinguishable_from_missing(api: Api):
    tenant_b_document_id, _ = api.seed_document(tenant_id=TENANT_B, kb_key="tenant_b")
    cross_tenant = api.client.get(f"/documents/{tenant_b_document_id}/versions", headers=api.token_headers())
    missing = api.client.get("/documents/" + "0" * 32 + "/versions", headers=api.token_headers())
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == missing.json()


# ════════════════════════════════════════════════════════════ 任务查询


def test_get_job_returns_pending_job(api: Api):
    body = api.upload().json()
    response = api.client.get(f"/ingestion-jobs/{body['job_id']}", headers=api.token_headers())
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "pending"
    assert job["stage"] == "received"
    assert job["error_code"] is None
    assert job["retry_count"] == 0
    assert job["document_id"] == body["document_id"]
    assert job["document_version_id"] is None
    assert job["warnings"] == []


def test_failed_job_is_queryable_and_retryable(api: Api):
    """任务失败可查询（含结构化错误码），并可经 /reprocess 新建 job 重试。"""

    body = api.upload().json()
    document_id, job_id = body["document_id"], body["job_id"]

    with db.session_scope(api.db_url) as session:
        repository.fail_ingestion_job(
            session,
            TENANT_A,
            job_id,
            stage="parsed",
            error_code="corrupt_document",
            error_message="PDF 结构损坏：缺少 xref 表",
        )
        repository.update_document_status(session, TENANT_A, document_id, "failed")

    failed = api.client.get(f"/ingestion-jobs/{job_id}", headers=api.token_headers()).json()
    assert failed["status"] == "failed"
    assert failed["stage"] == "parsed"
    assert failed["error_code"] == "corrupt_document"
    assert failed["error_message"].startswith("PDF 结构损坏")
    assert failed["retry_count"] == 1

    retried = api.client.post(f"/documents/{document_id}/reprocess", headers=api.token_headers())
    assert retried.status_code == 202
    new_job_id = retried.json()["job_id"]
    # 新建 job，不复用旧 job 改状态（retry_count 的语义是同一 job 的失败次数）
    assert new_job_id != job_id

    new_job = api.client.get(f"/ingestion-jobs/{new_job_id}", headers=api.token_headers()).json()
    assert (new_job["status"], new_job["stage"], new_job["retry_count"]) == ("pending", "received", 0)
    # 旧 job 的失败记录保持不变，仍可查询
    assert api.client.get(f"/ingestion-jobs/{job_id}", headers=api.token_headers()).json()["status"] == "failed"
    assert api.queue.pending_job_ids() == [job_id, new_job_id]


def test_job_exposes_parse_warnings_from_linked_version(api: Api):
    """解析警告从 metadata_json["warnings"] 读出并展开（形状同 ParseWarning.to_dict()）。"""

    document_id, job_id = api.seed_document()
    warnings = [
        {"code": "no_text_layer", "message": "疑似扫描件，未提取到文本层", "detail": {"ocr_engine": None}},
        {"code": "encoding_fallback", "message": "按 GBK 回退解码", "detail": {"encoding": "gbk"}},
    ]
    with db.session_scope(api.db_url) as session:
        version = repository.create_document_version(
            session,
            tenant_id=TENANT_A,
            document_id=document_id,
            content_hash="b" * 64,
            parser_name="pdf",
            parser_version="1.0",
            metadata_json={"warnings": warnings, "block_count": 0},
        )
        repository.update_ingestion_job(
            session, TENANT_A, job_id, status="succeeded", stage="parsed", document_version_id=version.id
        )

    response = api.client.get(f"/ingestion-jobs/{job_id}", headers=api.token_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["warnings"] == warnings
    assert body["document_version_id"] == version.id


def test_job_warnings_fall_back_to_latest_version(api: Api):
    """job 尚未绑定版本时，警告从文档最新版本读出 —— 保证警告一旦产生就可查询。"""

    document_id, job_id = api.seed_document()
    with db.session_scope(api.db_url) as session:
        repository.create_document_version(
            session,
            tenant_id=TENANT_A,
            document_id=document_id,
            content_hash="c" * 64,
            metadata_json={"warnings": [{"code": "empty_section", "message": "空章节"}]},
        )

    body = api.client.get(f"/ingestion-jobs/{job_id}", headers=api.token_headers()).json()
    assert body["document_version_id"] is None
    assert body["warnings"] == [{"code": "empty_section", "message": "空章节", "detail": None}]


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"warnings": "not-a-list"},
        {"warnings": ["not-a-dict", 42]},
    ],
)
def test_malformed_warnings_are_ignored_not_crashing(api: Api, metadata):
    document_id, job_id = api.seed_document()
    with db.session_scope(api.db_url) as session:
        version = repository.create_document_version(
            session,
            tenant_id=TENANT_A,
            document_id=document_id,
            content_hash="d" * 64,
            metadata_json=metadata,
        )
        repository.update_ingestion_job(session, TENANT_A, job_id, document_version_id=version.id)

    response = api.client.get(f"/ingestion-jobs/{job_id}", headers=api.token_headers())
    assert response.status_code == 200
    assert response.json()["warnings"] == []


def test_get_job_cross_tenant_is_indistinguishable_from_missing(api: Api):
    _document_id, tenant_b_job_id = api.seed_document(tenant_id=TENANT_B, kb_key="tenant_b")
    cross_tenant = api.client.get(f"/ingestion-jobs/{tenant_b_job_id}", headers=api.token_headers())
    missing = api.client.get("/ingestion-jobs/" + "0" * 32, headers=api.token_headers())
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == missing.json()


def test_get_job_requires_read_role(api: Api):
    _document_id, job_id = api.seed_document()
    response = api.client.get(f"/ingestion-jobs/{job_id}", headers=api.token_headers(roles=("agent",)))
    assert response.status_code == 403


# ════════════════════════════════════════════════════════════ 重新处理


def test_reprocess_requires_stricter_role_than_upload(api: Api):
    """重跑消耗索引资源，用的 operation 角色集合必须比上传更窄。"""

    body = api.upload().json()

    from services.auth_context import WRITE_OPERATION_ROLES

    upload_roles = WRITE_OPERATION_ROLES[documents_router.UPLOAD_OPERATION]
    reprocess_roles = WRITE_OPERATION_ROLES[documents_router.REPROCESS_OPERATION]
    assert reprocess_roles < upload_roles

    # knowledge_ops 能上传，但不能重处理
    assert api.upload(roles=("knowledge_ops",)).status_code == 202
    denied = api.client.post(
        f"/documents/{body['document_id']}/reprocess", headers=api.token_headers(roles=("knowledge_ops",))
    )
    assert denied.status_code == 403


def test_reprocess_writes_audit_and_does_not_touch_document_status(api: Api):
    body = api.upload().json()
    document_id = body["document_id"]
    with db.session_scope(api.db_url) as session:
        repository.update_document_status(session, TENANT_A, document_id, "published")

    response = api.client.post(f"/documents/{document_id}/reprocess", headers=api.token_headers())
    assert response.status_code == 202

    with db.session_scope(api.db_url) as session:
        document = repository.get_document(session, TENANT_A, document_id)
        assert document.status == "published"  # 旧版本仍在线上，不能被重处理「打回」
        events = repository.list_audit_events(session, TENANT_A, resource_type="document", resource_id=document_id)
        actions = [event.action for event in events]
        assert actions == ["document_upload", "document_reprocess"]
        assert events[-1].summary_json["job_id"] == response.json()["job_id"]


def test_reprocess_requires_knowledge_base_membership(api: Api):
    """文档所属知识库必须有写权限 —— 否则「别的库的成员」能重跑这个库的文档。"""

    document_id, _job_id = api.seed_document(kb_key="foreign")
    denied = api.client.post(f"/documents/{document_id}/reprocess", headers=api.token_headers())
    assert denied.status_code == 403
    assert api.queue.depth() == 0


def test_reprocess_cross_tenant_is_indistinguishable_from_missing(api: Api):
    tenant_b_document_id, _ = api.seed_document(tenant_id=TENANT_B, kb_key="tenant_b")
    cross_tenant = api.client.post(f"/documents/{tenant_b_document_id}/reprocess", headers=api.token_headers())
    missing = api.client.post("/documents/" + "0" * 32 + "/reprocess", headers=api.token_headers())
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == missing.json()


# ════════════════════════════════════════════════════════════ 队列不可用


def test_upload_returns_503_when_queue_is_unavailable(api: Api):
    """配置了 Redis 却连不上：返回 503，但 job 保留为 pending（不丢用户的操作痕迹）。"""

    class BrokenQueue:
        name = "broken"

        def publish(self, job_id: str) -> str:
            raise queue_module.QueueUnavailableError("connection refused")

        def depth(self) -> int:
            raise queue_module.QueueUnavailableError("connection refused")

    api.client.app.dependency_overrides[queue_module.get_queue] = lambda: BrokenQueue()
    response = api.upload()
    assert response.status_code == 503

    with db.session_scope(api.db_url) as session:
        jobs = repository.list_ingestion_jobs(session, TENANT_A)
        assert len(jobs) == 1
        assert (jobs[0].status, jobs[0].stage) == ("pending", "received")


# ════════════════════════════════════════════════════════════ OpenAPI


def test_openapi_contains_document_endpoints():
    """OpenAPI 导出把五个端点都带上，并沿用全局 bearerAuth 声明。"""

    import main

    schema = main.app.openapi()
    expected = {
        "/knowledge-bases/{knowledge_base_id}/documents": "post",
        "/documents/{document_id}": "get",
        "/documents/{document_id}/versions": "get",
        "/documents/{document_id}/reprocess": "post",
        "/ingestion-jobs/{job_id}": "get",
    }
    for path, method in expected.items():
        assert path in schema["paths"], path
        assert method in schema["paths"][path], (path, method)
        assert schema["paths"][path][method]["tags"] == ["documents"]
    assert schema["security"] == [{"bearerAuth": []}]
    assert "bearerAuth" in schema["components"]["securitySchemes"]
    # 上传端点必须声明 multipart 请求体（联调方按它写客户端）
    upload_body = schema["paths"]["/knowledge-bases/{knowledge_base_id}/documents"]["post"]["requestBody"]
    assert "multipart/form-data" in upload_body["content"]


# ════════════════════════════════════════════════════════════ 对象存储单元测试


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.txt", "cmd.txt"),
        ("/absolute/path/报告.docx", "报告.docx"),
        ("bad\x00name\n.md", "badname.md"),
        ('weird"name|pipe;.txt', "weird_name_pipe_.txt"),
        ("   .hidden.md  ", "hidden.md"),
        ("...", "upload"),
        ("", "upload"),
    ],
)
def test_sanitize_filename_removes_path_and_dangerous_characters(raw: str, expected: str):
    assert object_store.sanitize_filename(raw) == expected


def test_sanitize_filename_keeps_extension_when_truncating():
    long_name = "长" * 400 + ".pdf"
    cleaned = object_store.sanitize_filename(long_name)
    assert cleaned.endswith(".pdf")
    assert len(cleaned) <= object_store.MAX_FILENAME_LENGTH


def test_object_key_contains_tenant_and_document_id():
    key = object_store.build_object_key(TENANT_A, "doc123", "../../x.pdf")
    assert key == f"rag/{TENANT_A}/doc123/x.pdf"
    assert ".." not in key


def test_source_uri_round_trip():
    """流水线要靠 ``source_uri`` 反解文件名重建对象 key，这条往返关系是契约。"""

    uri = object_store.logical_source_uri("/tmp/客户 手册.md")
    assert object_store.filename_from_source_uri(uri) == "客户_手册.md"
    assert object_store.filename_from_source_uri("s3://bucket/key") is None


def test_local_object_store_round_trip(tmp_path):
    store = object_store.LocalObjectStore(tmp_path / "store")
    key = object_store.build_object_key(TENANT_A, "doc1", "a.pdf")

    assert store.exists(key) is False
    assert store.put(key, b"payload", content_type="application/pdf") == key
    assert store.exists(key) is True
    assert store.get(key) == b"payload"
    # 写入必须原子：临时文件不能残留
    assert [path.name for path in (store.root / key).parent.iterdir() if path.name.endswith(".tmp")] == []

    store.delete(key)
    assert store.exists(key) is False
    store.delete(key)  # 幂等：重复删除不报错


def test_local_object_store_rejects_escaping_keys(tmp_path):
    store = object_store.LocalObjectStore(tmp_path / "store")
    for key in ("../escape.txt", "/etc/passwd", "a/../../b.txt", "", "a\\b.txt"):
        with pytest.raises(object_store.ObjectStoreError):
            store.put(key, b"x")


def test_local_object_store_get_missing_raises(tmp_path):
    store = object_store.LocalObjectStore(tmp_path / "store")
    with pytest.raises(object_store.ObjectStoreError):
        store.get("rag/missing.txt")


def test_build_object_store_uses_configured_root(tmp_path, monkeypatch):
    monkeypatch.setenv(object_store.ROOT_ENV, str(tmp_path / "custom"))
    store = object_store.build_object_store()
    assert store.name == "local"
    assert store.root == (tmp_path / "custom").resolve()


def test_build_object_store_rejects_unknown_driver(monkeypatch):
    monkeypatch.setenv(object_store.DRIVER_ENV, "s3")
    with pytest.raises(object_store.ObjectStoreError):
        object_store.build_object_store()


def test_build_object_store_resolves_relative_root_under_project():
    store = object_store.build_object_store({object_store.ROOT_ENV: "data/object_store_unit"})
    assert store.root == (object_store.PROJECT_ROOT / "data/object_store_unit").resolve()


# ════════════════════════════════════════════════════════════ 内容嗅探单元测试


def test_sniff_detects_pdf_and_docx_and_html():
    assert content_sniff.sniff_content_type(PDF_PAYLOAD) == "pdf"
    assert content_sniff.sniff_content_type(DOCX_PAYLOAD) == "docx"
    assert content_sniff.sniff_content_type(HTML_PAYLOAD) == "html"
    assert content_sniff.sniff_content_type(b"\xef\xbb\xbf  \n<HTML>") == "html"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        TXT_PAYLOAD,
        MD_PAYLOAD,
        b"\x89PNG\r\n\x1a\n",
        b"PK\x03\x04truncated-not-a-real-zip",
    ],
)
def test_sniff_returns_none_when_it_cannot_be_certain(data: bytes):
    assert content_sniff.sniff_content_type(data) is None


def test_sniff_rejects_zip_that_is_not_a_docx():
    assert content_sniff.sniff_content_type(_zip_bytes({"ppt/presentation.xml": b"x"})) is None


def test_matches_declared_type_only_enforces_strong_formats():
    assert content_sniff.matches_declared_type("txt", b"any bytes at all") is True
    assert content_sniff.matches_declared_type("md", HTML_PAYLOAD) is True
    assert content_sniff.matches_declared_type("html", b"no html here") is True
    assert content_sniff.matches_declared_type("pdf", PDF_PAYLOAD) is True
    assert content_sniff.matches_declared_type("pdf", HTML_PAYLOAD) is False
    assert content_sniff.matches_declared_type("docx", DOCX_PAYLOAD) is True
    assert content_sniff.matches_declared_type("docx", PDF_PAYLOAD) is False


# ════════════════════════════════════════════════════════════ 队列单元测试


class FakeRedis:
    """最小假客户端：只实现队列用到的 XADD / XLEN。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.streams: dict[str, list[dict]] = {}

    def xadd(self, key: str, fields: dict) -> str:
        self.calls.append((key, dict(fields)))
        self.streams.setdefault(key, []).append(dict(fields))
        return f"{len(self.streams[key])}-0"

    def xlen(self, key: str) -> int:
        return len(self.streams.get(key, []))


def test_memory_queue_keeps_order_and_reports_depth():
    memory = queue_module.InMemoryIngestionQueue()
    assert memory.name == "memory"
    assert memory.depth() == 0
    memory.publish("job-1")
    memory.publish("job-2")
    assert memory.depth() == 2
    assert memory.pending_job_ids() == ["job-1", "job-2"]
    with pytest.raises(queue_module.QueueError):
        memory.publish("  ")


def test_build_queue_uses_memory_when_redis_is_not_configured():
    built = queue_module.build_queue({})
    assert isinstance(built, queue_module.InMemoryIngestionQueue)


def test_build_queue_uses_redis_when_url_is_configured():
    built = queue_module.build_queue(
        {
            queue_module.STREAM_URL_ENV: "redis://127.0.0.1:6379/0",
            queue_module.STREAM_KEY_ENV: "custom:stream",
            queue_module.CONSUMER_GROUP_ENV: "custom-workers",
        }
    )
    assert isinstance(built, queue_module.RedisStreamIngestionQueue)
    assert built.stream_key == "custom:stream"
    assert built.consumer_group == "custom-workers"
    assert built.name == "redis-stream:custom:stream"


def test_redis_queue_publishes_to_stream_with_injected_client():
    client = FakeRedis()
    redis_queue = queue_module.RedisStreamIngestionQueue("redis://127.0.0.1:6379/0", client=client)

    assert redis_queue.publish("job-42") == "1-0"
    assert client.calls == [(queue_module.DEFAULT_STREAM_KEY, {queue_module.PAYLOAD_FIELD: "job-42"})]
    assert redis_queue.depth() == 1


def test_redis_queue_reports_unavailable_when_client_is_missing(monkeypatch):
    """配了 Redis 但没装客户端 -> 明确报不可用（不许静默退回内存队列）。"""

    monkeypatch.setitem(sys.modules, "redis", None)
    redis_queue = queue_module.RedisStreamIngestionQueue("redis://127.0.0.1:6379/0")
    with pytest.raises(queue_module.QueueUnavailableError):
        redis_queue.publish("job-1")


def test_redis_queue_wraps_client_errors(monkeypatch):
    class FailingRedis(FakeRedis):
        def xadd(self, key: str, fields: dict) -> str:
            raise RuntimeError("connection refused")

    redis_queue = queue_module.RedisStreamIngestionQueue("redis://127.0.0.1:6379/0", client=FailingRedis())
    with pytest.raises(queue_module.QueueUnavailableError):
        redis_queue.publish("job-1")


def test_get_queue_is_cached_and_resettable(monkeypatch):
    monkeypatch.delenv(queue_module.STREAM_URL_ENV, raising=False)
    queue_module.reset_queue()
    try:
        first = queue_module.get_queue()
        assert first is queue_module.get_queue()
        first.publish("job-1")
        assert queue_module.get_queue().depth() == 1
    finally:
        queue_module.reset_queue()
    assert queue_module.get_queue() is not first


# ════════════════════════════════════════════════════════════ 工具


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()
