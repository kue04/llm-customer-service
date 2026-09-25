"""检索 API（计划 4.3，批次 B7）。

两条路径，**入口即见分野**（踩坑 F1：两条路径都叫"检索"、
调用方不知道哪条是真的，是这个项目已经踩过一次的坑）
-----------------------------------------------------------------
```
POST /retrieval/search            ← 【正式】chunk 级检索（B 轨）
     文档 chunk 索引 + **服务端构造的权限过滤**（tenant / ACL / 发布状态）
     没有生效索引 → 503 + error_code=chunk_index_unavailable

POST /retrieval/search-demo       ← 【演示 / 兼容】种子 FAQ 检索（A 轨）
POST /retrieval/prompt-preview      手工整理的 781 条 FAQ，**没有 tenant / ACL 概念**
                                    （它读的是单租户种子语料，压根没带租户与 ACL 的数据）
```
响应里带 ``retrieval_path``（``chunk-index`` / ``seed-faq-demo``），
调用方不必猜自己命中哪条路。README 里也写明了哪条是真的。

授权（4.1 的判定顺序，前四步在这里）
-----------------------------------
读令牌 → 租户匹配 → scope（``read:retrieval_read``）→ 知识库成员 / document ACL
→ 由 ``services/retrieval_access.py`` 构造 :class:`ChunkAccessFilter` 注入检索。
**filter 只在这里由 AuthContext 构造**，客户端能传 filter 就等于能提权 ——
请求模型里因此没有任何 tenant / acl / filter / row_id 字段。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from config.rag_config import get_rag_config_dict
from models.prompt import create_prompt
from schemas.retrieval_schema import (
    ChunkIndexInfo,
    ChunkRetrievalItem,
    ChunkRetrievalRequest,
    ChunkRetrievalResponse,
    PromptPreviewResponse,
    RagConfigResponse,
    RetrievalResultItem,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from services.auth_service import AuthContext, get_auth_context, require_read_operation_role
from services.ingestion.db import session_scope
from services.ingestion.pipeline import default_embedder, default_embedding_model, default_index_root
from services.retrieval_access import build_chunk_access_filter
from utils.hybrid_retriever import retrieve_hybrid_items
from utils.rag_context import build_prompt_context_items
from utils.sparse_retriever import ERROR_SPARSE_UNAVAILABLE
from utils.vector_retriever import (
    CHUNK_INDEX_NAME,
    ERROR_EMBEDDING_MODEL_MISMATCH,
    ERROR_INDEX_UNAVAILABLE,
    ChunkRetrievalError,
    describe_chunk_index,
    retrieve_by_real_vector,
    retrieve_chunk_items,
)


router = APIRouter()

#: B 轨检索的**默认模式**。
#: 默认使用 ``hybrid``：正式索引必须同时具备 dense 与 sparse 两路；旧索引不可用时显式失败，避免静默退回导致质量口径漂移。
#:
#: 生效索引缺少稀疏路时明确返回 503，不静默退回 dense；dense 可显式选择用于诊断。
DEFAULT_RETRIEVAL_MODE = "hybrid"

#: 正式检索路径读的 operation 名（权限表里的 ``retrieval_read``）
READ_OPERATION = "retrieval_read"

#: 稳定 error_code → HTTP 状态码。
#: 刻意**不**把任何检索错误一律映射成 500：
#: * ``chunk_index_unavailable`` —— 索引还没建 / 指针缺失，是**服务可用性**问题 → 503，
#:   调用方可以稍后重试，也不该被当成"服务器 bug"报给用户；
#: * ``embedding_model_mismatch`` —— 查询模型与索引模型不一致，是**部署配置**
#:   问题 → 500（服务端错，不是调用方的错，重试也没用），要告警；
#: * ``chunk_filter_*`` —— 契约违例（filter 没构造就调了检索），是**编程错误**
#:   → 500；这条从设计上不该在生产出现，出现即说明代码写错了。
STATUS_BY_ERROR_CODE: dict[str, int] = {
    ERROR_INDEX_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    # 稀疏索引不可用（manifest 未声明 / 文件缺失 / 与 manifest 错配）：
    # 与 ``chunk_index_unavailable`` 同类 —— 是**服务可用性**问题，
    # 调用方可以稍后重试（例如运维刚重建完索引），不是调用方写错了。
    ERROR_SPARSE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ERROR_EMBEDDING_MODEL_MISMATCH: status.HTTP_500_INTERNAL_SERVER_ERROR,
    "chunk_filter_required": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "chunk_filter_tenant_missing": status.HTTP_500_INTERNAL_SERVER_ERROR,
    # 稀疏索引与 manifest 错配是**部署事故**（文件被换/拷错），重试无用 → 500
    "sparse_index_mismatch": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _translate_chunk_retrieval_error(error: ChunkRetrievalError) -> HTTPException:
    """把检索层的稳定 ``error_code`` 翻译成状态码 + 同一个错误码回给调用方。"""

    status_code = STATUS_BY_ERROR_CODE.get(error.error_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error.error_code, "message": error.message},
    )


@router.get("/config", response_model=RagConfigResponse)
def get_retrieval_config(auth: AuthContext = Depends(get_auth_context)) -> dict:
    require_read_operation_role(READ_OPERATION, auth)
    return get_rag_config_dict()


# ================================================================ 正式路径（B 轨）


def _chunk_hit_item(rank: int, item: dict) -> ChunkRetrievalItem:
    return ChunkRetrievalItem(
        rank=rank,
        chunk_id=item["chunk_id"],
        document_id=item["document_id"],
        document_version=item["document_version"],
        document_version_id=item["document_version_id"],
        tenant_id=item["tenant_id"],
        title=item["title"],
        document_title=item["document_title"],
        chunk_type=item["chunk_type"],
        heading_path=list(item["heading_path"]),
        page_start=item["page_start"],
        page_end=item["page_end"],
        acl=[dict(entry) for entry in item["acl"]],
        source_uri=item["source_uri"],
        filename=item["filename"],
        source_type=item["source_type"],
        content_hash=item["content_hash"],
        token_count=item["token_count"],
        text=item["text"],
        score=item["score"],
        retrieval_origin=item["retrieval_origin"],
        # 路由溯源：``dense`` 模式下这两项为 None（该模式本就没有第二路）
        dense_rank=item.get("dense_rank"),
        sparse_rank=item.get("sparse_rank"),
        routes=list(item.get("routes") or []),
    )


@router.post(
    "/search",
    response_model=ChunkRetrievalResponse,
    summary="检索文档 chunk（正式路径，按租户与 document ACL 过滤）",
)
def search_retrieval_chunks(
    request: ChunkRetrievalRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ChunkRetrievalResponse:
    """正式的检索接口：**先授权，再检索**。

    三个必须按顺序理解的点：

    1. ``access`` 过滤器在**服务端**构造（``build_chunk_access_filter``），
       输入只有已校验的 ``AuthContext`` 与库里的 ACL / 版本状态；
    2. 过滤发生在**候选暴露之前**（FAISS ``IDSelectorBatch`` 预过滤；
       稀疏路是 FTS5 临时表 JOIN 预过滤）—— 不是"先全局 top-k 再筛"，
       后者在长尾租户上会静默返回 0 条；
    3. 零命中是**正常结果**（无权限 = 零命中，不返回 403），
       因为返回 403 会泄漏"这条文档存在但你没权限"。

    ``retrieval_mode`` 决定走哪条召回路径（见 :data:`DEFAULT_RETRIEVAL_MODE`）。
    无论哪种模式，**权限过滤都在检索层内、候选暴露之前完成** —— 混合检索
    不是"绕过隔离的第二条路"，它复用的是同一个 :class:`ChunkAccessFilter`。
    """

    require_read_operation_role(READ_OPERATION, auth)

    mode = request.retrieval_mode or DEFAULT_RETRIEVAL_MODE
    if mode != "dense" and request.min_score is not None:
        # 显式拒绝而不是静默忽略。``min_score`` 是余弦相似度阈值（[0,1]），
        # 而 hybrid/sparse 的分数是 RRF 融合分 / ``-bm25``，量纲完全不同。
        # 若改为静默忽略，调用方会以为自己设的阈值生效了 —— 这类"设置被悄悄丢弃"
        # 是排查成本最高的一类问题。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "min_score_not_supported",
                "message": (
                    f"mode={mode} 的分数不是余弦相似度（hybrid 是 RRF 融合分，"
                    "sparse 是 -bm25），不支持 min_score；请改用 mode=dense，"
                    "或通过融合权重控制质量"
                ),
            },
        )

    index_root = default_index_root()
    with session_scope() as session:
        access = build_chunk_access_filter(session, auth)
        try:
            if mode == "dense":
                items = retrieve_chunk_items(
                    request.query,
                    access=access,
                    limit=request.limit,
                    min_score=request.min_score,
                    embedder=default_embedder,
                    embedding_model=default_embedding_model(),
                    root=index_root,
                    index_name=CHUNK_INDEX_NAME,
                )
            else:
                items = retrieve_hybrid_items(
                    request.query,
                    access=access,
                    limit=request.limit,
                    mode=mode,
                    embedder=default_embedder,
                    embedding_model=default_embedding_model(),
                    root=index_root,
                    index_name=CHUNK_INDEX_NAME,
                )
            index_info = describe_chunk_index(root=index_root, index_name=CHUNK_INDEX_NAME)
        except ChunkRetrievalError as error:
            raise _translate_chunk_retrieval_error(error) from error

    visible = access.allowed_chunk_ids or frozenset()
    results = [_chunk_hit_item(item["rank"], item) for item in items]
    return ChunkRetrievalResponse(
        query=request.query,
        count=len(results),
        retrieval_mode=mode,
        index=ChunkIndexInfo(
            index_name=str(index_info["index_name"]),
            index_version=int(index_info["index_version"]),
            embedding_model=str(index_info["embedding_model"]),
            embedding_dimension=int(index_info["embedding_dimension"]),
            built_at=str(index_info["built_at"]),
            chunk_count=int(index_info["chunk_count"]),
            tokenizer_id=str(index_info.get("tokenizer_id", "")),
            visible_chunk_count=len(visible),
            sparse_available=bool(index_info.get("sparse_available", False)),
            sparse_index_file=str(index_info.get("sparse_index_file", "")),
            sparse_gram_algorithm=str(index_info.get("sparse_gram_algorithm", "")),
        ),
        results=results,
    )


# ================================================================ 演示 / 兼容路径（A 轨）


def build_retrieval_result_item(rank: int, item: dict) -> RetrievalResultItem:
    source = item["source"]
    return RetrievalResultItem(
        rank=rank,
        score=item["score"],
        rerank_score=item["rerank_score"],
        model_rerank_score=item["model_rerank_score"],
        vector_score=item["vector_score"],
        keyword_bonus=item["keyword_bonus"],
        direction_penalty=item.get("direction_penalty", 0.0),
        category=source.get("category", ""),
        intent=source.get("intent", ""),
        question=source.get("question", ""),
        answer=item["answer"],
    )


@router.post(
    "/search-demo",
    response_model=RetrievalSearchResponse,
    summary="【演示 / 兼容】检索种子 FAQ（A 轨，无权限过滤）",
    description=(
        "**这不是正式检索路径。** 它读的是手工整理的 781 条种子 FAQ"
        "（`data/takeout_customer_service_seed.jsonl`），语料本身是单租户的，"
        "**没有 tenant / ACL 过滤**（`retrieve_*` 系列签名里就没有这两个参数）。\n\n"
        "正式路径是 `POST /retrieval/search`（chunk 级 + 服务端权限过滤）。\n"
        "保留本端点只是为了演示检索分数构成与兼容旧调试台。"
    ),
)
def search_retrieval_demo(
    request: RetrievalSearchRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> RetrievalSearchResponse:
    """【演示 / 兼容】A 轨：种子 FAQ 检索，**无 tenant / ACL 过滤**。

    ``retrieve_by_real_vector`` 读的是种子语料（``iter_knowledge_items``），
    它的签名里没有 tenant / acl —— 不是"忘了加"，而是数据源本来就没有这两维。
    正式路径见 :func:`search_retrieval_chunks`。
    """

    require_read_operation_role(READ_OPERATION, auth)
    candidates = retrieve_by_real_vector(
        request.query,
        limit=request.limit,
        min_score=request.min_score,
        use_hybrid=request.mode == "hybrid",
    )

    results = []
    for rank, item in enumerate(candidates, start=1):
        results.append(build_retrieval_result_item(rank, item))

    return RetrievalSearchResponse(
        query=request.query,
        mode=request.mode,
        count=len(results),
        results=results,
    )


@router.post(
    "/prompt-preview",
    response_model=PromptPreviewResponse,
    summary="【演示 / 兼容】拼装 RAG prompt（基于 A 轨种子 FAQ，无权限过滤）",
    description=(
        "与 `POST /retrieval/search-demo` 同一条链路（种子 FAQ，无权限过滤），"
        "只额外返回最终拼好的 prompt，供调试台观察证据如何进上下文。\n"
        "正式检索路径是 `POST /retrieval/search`。"
    ),
)
def preview_demo_prompt(
    request: RetrievalSearchRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> PromptPreviewResponse:
    """【演示 / 兼容】A 轨的 prompt 预览（无 tenant / ACL 过滤）。"""

    require_read_operation_role(READ_OPERATION, auth)
    candidates = retrieve_by_real_vector(
        request.query,
        limit=request.limit,
        min_score=request.min_score,
        use_hybrid=request.mode == "hybrid",
    )
    results = []
    for rank, item in enumerate(candidates, start=1):
        results.append(build_retrieval_result_item(rank, item))

    prompt_context_items = build_prompt_context_items(
        [result.model_dump() for result in results],
        max_items=request.limit,
    )
    prompt_context_results = [
        RetrievalResultItem(
            role=item.role,
            evidence_strength=item.evidence_strength,
            display_title=item.display_title,
            evidence_summary=item.evidence_summary,
            prompt_instruction=item.prompt_instruction,
            source_question=item.source_question,
            source_answer=item.source_answer,
            rank=item.rank,
            score=item.score,
            rerank_score=item.rerank_score,
            model_rerank_score=0.0,
            vector_score=0.0,
            keyword_bonus=0.0,
            direction_penalty=0.0,
            category=item.category,
            intent=item.intent,
            question=item.question,
            answer=item.answer,
        )
        for item in prompt_context_items
    ]

    prompt = create_prompt(request.query, prompt_context_items)

    return PromptPreviewResponse(
        query=request.query,
        mode=request.mode,
        count=len(results),
        prompt=prompt,
        prompt_context_items=prompt_context_results,
        results=results,
    )
