"""F6：生效 manifest 的进程内缓存（2026-09-25）。

为什么这个文件存在
------------------
F6 实测（``scripts/audit_manifest_load_cost.py``）：``read_manifest`` 单次
**约 100~120ms**（19.0MB / 9229 条正文的 JSON 反序列化 + 对象构造），
而同一份索引的 ``faiss.read_index`` 只要 **约 5ms**；每次检索都要走一次，
``hybrid`` 一次请求要走**两次**（稠密一路、稀疏一路）。这就是
「延迟被'把数据搬进来'的成本主导，而不是被检索算法主导」的量化依据。

缓存是**用正确性换速度**的典型部位，所以本文件测的重点不是"变快了"，
而是**四条会产生静默错误的边界**：

1. 命中缓存 —— 第二次不再重读文件；
2. **版本切换必须失效** —— 失效信号是**指针里的版本号/指纹**，不是 mtime，
   更不是"等一会儿"；
3. **缓存键的分辨率** —— ``root`` / ``index_name`` 少一维就会**串味**；
4. **只读语义不变** —— 命中缓存不写盘、不改动已生效索引。

两条断言纪律（承踩坑 D20 / B22 / B20）：
* "没有重读文件"用**计数包装器**实测，不用耗时（耗时在 CI 上会飘）；
* "读到的是新版本"用**内容**（chunk_id / 文本），不用对象身份
  （``is`` 相同只能说明 Python 复用了引用，说明不了加载的是哪一版）。
"""

from __future__ import annotations

import json
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from retrieval_fixtures import (
    INDEX_NAME,
    RetrievalEnv,
    TENANT_A,
    alembic_upgrade,
    fake_embedding,
    query_for,
)
from services.ingestion import db, index_manifest
from services.ingestion.index_manifest import (
    MANIFEST_FILENAME,
    MAX_CACHED_MANIFESTS,
    IndexManifest,
    ManifestEntry,
    active_manifest_cache_stats,
    load_active_manifest,
    pointer_path,
    reset_active_manifest_cache,
    switch_pointer,
    version_dir,
    write_manifest,
)
from services.ingestion.object_store import LocalObjectStore


@pytest.fixture(autouse=True)
def clean_cache():
    """缓存是进程级的 —— 每个用例前后都清一次，避免用例之间互相"借到"对方的 manifest。"""

    reset_active_manifest_cache()
    yield
    reset_active_manifest_cache()


# ---------------------------------------------------------------- 夹具：手写一份索引目录


def make_manifest(*, index_name: str = INDEX_NAME, version: int, chunks: list[str]) -> IndexManifest:
    """只造 manifest（不需要真实向量文件）—— ``load_active_manifest`` 不读向量。"""

    entries = tuple(
        ManifestEntry(
            row_id=position,
            chunk_id=chunk_id,
            tenant_id=TENANT_A,
            document_id=f"doc-{chunk_id}",
            document_version=1,
            document_version_id=f"ver-{chunk_id}",
            chunk_type="child",
            ordinal=position,
            text=f"{chunk_id} 的正文",
        )
        for position, chunk_id in enumerate(chunks)
    )
    return IndexManifest(
        index_name=index_name,
        tenant_id=TENANT_A,
        index_version=version,
        embedding_model="fake-embedder-v1",
        embedding_dimension=16,
        built_at="2026-09-25T00:00:00",
        chunk_ids=tuple(chunks),
        entries=entries,
    )


def publish(root: Path, manifest: IndexManifest, *, build_id: str = "") -> None:
    """把一份 manifest 写成正式版本 + 原子切换指针（与 ``index_builder`` 同路径）。"""

    directory = version_dir(root, manifest.index_name, manifest.index_version)
    directory.mkdir(parents=True, exist_ok=True)
    write_manifest(directory, manifest)
    switch_pointer(
        root,
        index_name=manifest.index_name,
        tenant_id=TENANT_A,
        index_version=manifest.index_version,
        manifest_relpath=f"{index_manifest.CHUNK_INDEX_DIRNAME}/{manifest.index_name}/"
        f"{index_manifest.version_dir_name(manifest.index_version)}/{MANIFEST_FILENAME}",
        index_relpath=f"{index_manifest.CHUNK_INDEX_DIRNAME}/{manifest.index_name}/"
        f"{index_manifest.version_dir_name(manifest.index_version)}/{index_manifest.INDEX_FILENAME}",
        fingerprint=manifest.fingerprint(),
        build_id=build_id,
    )


def load_chunk_ids(root: Path, index_name: str = INDEX_NAME) -> list[str]:
    manifest, _pointer, _index_file = load_active_manifest(root, index_name)
    return [entry.chunk_id for entry in manifest.entries]


# ---------------------------------------------------------------- 一、命中缓存


class TestCacheHit:
    def test_second_load_does_not_reread_the_manifest_file(self, tmp_path, monkeypatch) -> None:
        """★ 核心用例：第二次加载不再走 ``read_manifest``（实测计数，不测耗时）。"""

        publish(tmp_path, make_manifest(version=1, chunks=["c_1", "c_2"]))

        calls = {"n": 0}
        real = index_manifest.read_manifest

        def counting(path):
            calls["n"] += 1
            return real(path)

        monkeypatch.setattr(index_manifest, "read_manifest", counting)

        first = load_chunk_ids(tmp_path)
        second = load_chunk_ids(tmp_path)
        third = load_chunk_ids(tmp_path)

        assert first == second == third == ["c_1", "c_2"]
        assert calls["n"] == 1, f"manifest 被重读了 {calls['n']} 次，缓存没生效"

    def test_cache_stats_separate_hits_from_misses(self, tmp_path) -> None:
        publish(tmp_path, make_manifest(version=1, chunks=["c_1"]))
        load_chunk_ids(tmp_path)
        load_chunk_ids(tmp_path)
        assert active_manifest_cache_stats() == {"hit": 1, "miss": 1}

    def test_pointer_is_always_read_from_disk(self, tmp_path) -> None:
        """指针**每次都要真读** —— 它是失效信号的载体，缓存指针等于把失效判据缓存掉。

        用例形态：先命中一次缓存，再切指针 —— 若把指针也缓存了，第二次会看不到新版本。
        """

        publish(tmp_path, make_manifest(version=1, chunks=["c_1"]))
        load_chunk_ids(tmp_path)
        payload = json.loads(pointer_path(tmp_path, INDEX_NAME).read_text(encoding="utf-8"))
        assert payload["index_version"] == 1

        publish(tmp_path, make_manifest(version=2, chunks=["c_9"]))
        assert load_chunk_ids(tmp_path) == ["c_9"]


# ---------------------------------------------------------------- 二、版本切换即失效


class TestVersionInvalidation:
    def test_switch_to_new_version_invalidates_cache(self, tmp_path) -> None:
        publish(tmp_path, make_manifest(version=1, chunks=["c_old"]))
        assert load_chunk_ids(tmp_path) == ["c_old"]

        publish(tmp_path, make_manifest(version=2, chunks=["c_new"]))
        assert load_chunk_ids(tmp_path) == ["c_new"], "版本已切，缓存仍返回旧 manifest"

    def test_rollback_to_older_version_reloads_that_version(self, tmp_path) -> None:
        """回滚是"再写一次指针"，走的是同一段失效逻辑。"""

        publish(tmp_path, make_manifest(version=1, chunks=["c_v1"]))
        publish(tmp_path, make_manifest(version=2, chunks=["c_v2"]))
        assert load_chunk_ids(tmp_path) == ["c_v2"]

        publish(tmp_path, make_manifest(version=1, chunks=["c_v1"]))
        assert load_chunk_ids(tmp_path) == ["c_v1"]

    def test_same_version_with_new_fingerprint_is_reread(self, tmp_path) -> None:
        """版本号没变但内容被改写 → 指纹变 → **必须重读**（不靠版本号一条腿走路）。"""

        publish(tmp_path, make_manifest(version=1, chunks=["c_1"]))
        assert load_chunk_ids(tmp_path) == ["c_1"]

        # 原地重写同一个版本目录（版本目录按设计不可变，这里刻意构造违规场景）
        version_one = version_dir(tmp_path, INDEX_NAME, 1)
        rewritten = make_manifest(version=1, chunks=["c_1", "c_tampered"])
        write_manifest(version_one, rewritten)
        switch_pointer(
            tmp_path,
            index_name=INDEX_NAME,
            tenant_id=TENANT_A,
            index_version=1,
            manifest_relpath=f"{index_manifest.CHUNK_INDEX_DIRNAME}/{INDEX_NAME}/v1/{MANIFEST_FILENAME}",
            index_relpath=f"{index_manifest.CHUNK_INDEX_DIRNAME}/{INDEX_NAME}/v1/{index_manifest.INDEX_FILENAME}",
            fingerprint=rewritten.fingerprint(),
        )
        assert load_chunk_ids(tmp_path) == ["c_1", "c_tampered"]


# ---------------------------------------------------------------- 三、键的分辨率


class TestKeyResolution:
    def test_two_index_names_do_not_mix(self, tmp_path) -> None:
        """少 ``index_name`` 这一维会串味：查 A 拿到 B 的 chunk_id（静默、不报错）。"""

        publish(tmp_path, make_manifest(index_name="index_a", version=1, chunks=["c_a"]))
        publish(tmp_path, make_manifest(index_name="index_b", version=1, chunks=["c_b"]))

        assert load_chunk_ids(tmp_path, "index_a") == ["c_a"]
        assert load_chunk_ids(tmp_path, "index_b") == ["c_b"]
        # 再读一遍 A：缓存不能把 A 的位置让给 B
        assert load_chunk_ids(tmp_path, "index_a") == ["c_a"]

    def test_two_roots_do_not_mix(self, tmp_path) -> None:
        root_a = tmp_path / "root-a"
        root_b = tmp_path / "root-b"
        publish(root_a, make_manifest(version=1, chunks=["c_root_a"]))
        publish(root_b, make_manifest(version=1, chunks=["c_root_b"]))

        assert load_chunk_ids(root_a) == ["c_root_a"]
        assert load_chunk_ids(root_b) == ["c_root_b"]
        assert load_chunk_ids(root_a) == ["c_root_a"]

    def test_same_root_written_differently_hits_the_same_entry(self, tmp_path) -> None:
        """``./x`` 与 ``x/../x`` 是同一个索引 —— 规范化后必须命中同一条缓存。"""

        publish(tmp_path, make_manifest(version=1, chunks=["c_1"]))
        assert load_chunk_ids(tmp_path) == ["c_1"]
        assert load_chunk_ids(tmp_path / "." / ".." / tmp_path.name) == ["c_1"]
        assert active_manifest_cache_stats()["miss"] == 1


# ---------------------------------------------------------------- 四、只读语义与容量


class TestReadOnlyAndBounds:
    def test_cache_hit_does_not_touch_disk(self, tmp_path) -> None:
        publish(tmp_path, make_manifest(version=1, chunks=["c_1", "c_2"]))
        manifest_file = version_dir(tmp_path, INDEX_NAME, 1) / MANIFEST_FILENAME

        def snapshot() -> tuple[float, int, str]:
            stat = manifest_file.stat()
            return stat.st_mtime_ns, stat.st_size, manifest_file.read_text(encoding="utf-8")

        before = snapshot()
        root_files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))

        for _ in range(3):
            load_chunk_ids(tmp_path)

        assert snapshot() == before, "命中缓存却改动了磁盘上的 manifest"
        assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == root_files

    def test_cache_entries_are_bounded(self, tmp_path) -> None:
        """版本不断递增时缓存不能无限长（每份 19MB，长跑服务会吃光内存）。"""

        for version in range(1, MAX_CACHED_MANIFESTS + 6):
            publish(tmp_path, make_manifest(version=version, chunks=[f"c_v{version}"]))
            assert load_chunk_ids(tmp_path) == [f"c_v{version}"]

        assert len(index_manifest._ACTIVE_MANIFEST_CACHE) <= MAX_CACHED_MANIFESTS

    def test_cached_manifest_is_shared_but_immutable(self, tmp_path) -> None:
        """共享是有意为之 —— 前提是 manifest 不可变（frozen dataclass + tuple）。"""

        publish(tmp_path, make_manifest(version=1, chunks=["c_1"]))
        first, _pointer, _path = load_active_manifest(tmp_path, INDEX_NAME)
        second, _pointer, _path = load_active_manifest(tmp_path, INDEX_NAME)
        assert first is second
        with pytest.raises(FrozenInstanceError):
            first.index_version = 999  # type: ignore[misc]


class TestConcurrency:
    def test_concurrent_loads_return_the_same_manifest(self, tmp_path) -> None:
        """两个请求同时未命中：都读到同一份内容，且不出现"半个对象"。"""

        publish(tmp_path, make_manifest(version=1, chunks=["c_1", "c_2", "c_3"]))
        results: list[list[str]] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                results.append(load_chunk_ids(tmp_path))
            except BaseException as error:  # noqa: BLE001 - 线程里的异常要带回主线程断言
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors, errors
        assert results == [["c_1", "c_2", "c_3"]] * 8


# ---------------------------------------------------------------- 五、端到端：切版本后第一次检索


class TestRetrievalSeesNewIndex:
    """★ 交付判据点名的用例：**切版本后第一次检索就要读到新索引**。

    这里必须走真实索引（FAISS 文件 + 真实流水线），因为要证的正是
    "缓存让检索层用了旧 manifest" —— 而旧 manifest 里的 ``entries`` 里
    根本没有新文档的 chunk，症状是**返回空**（不报错）。
    """

    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        url = f"sqlite:///{(tmp_path / 'cache.db').as_posix()}"
        monkeypatch.setenv(db.DATABASE_URL_ENV, url)
        alembic_upgrade(url)
        engine = db.create_db_engine(url)
        session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
        index_root = tmp_path / "faiss_store"
        environment = RetrievalEnv(session, LocalObjectStore(tmp_path / "objects"), index_root)
        try:
            yield environment, index_root
        finally:
            session.close()
            engine.dispose()
            db.dispose_engines()

    def _search(self, index_root: Path, tag: str):
        from utils.vector_retriever import ChunkAccessFilter, search_chunk_index

        return search_chunk_index(
            query_for(tag),
            access=ChunkAccessFilter(tenant_id=TENANT_A),
            top_k=5,
            embedder=fake_embedding,
            embedding_model="fake-embedder-v1",
            root=index_root,
            index_name=INDEX_NAME,
        )

    def test_first_search_after_switch_reads_the_new_index(self, env) -> None:
        environment, index_root = env
        environment.ingest(TENANT_A, tag="alpha")
        assert self._search(index_root, "alpha"), "第一版索引里就该能查到 alpha"

        # 旧版本已进缓存；新入库 + 重建后指针指向新版本
        environment.ingest(TENANT_A, tag="beta", filename=f"{TENANT_A}-beta-2.md")
        environment.rebuild(TENANT_A)

        hits = self._search(index_root, "beta")
        assert hits, "切版本后第一次检索返回空 —— 说明用的是已被切走的旧 manifest"
        assert any("beta" in hit.text for hit in hits)

        _manifest, pointer, _path = load_active_manifest(index_root, INDEX_NAME)
        assert pointer.index_version > 1

    def test_reported_version_matches_the_manifest_actually_used(self, env) -> None:
        """``describe_chunk_index`` 报的版本号必须与检索真正用的 manifest 一致。"""

        from utils.vector_retriever import describe_chunk_index

        environment, index_root = env
        environment.ingest(TENANT_A, tag="alpha")
        first = describe_chunk_index(root=index_root, index_name=INDEX_NAME)
        environment.ingest(TENANT_A, tag="beta", filename=f"{TENANT_A}-beta-2.md")
        environment.rebuild(TENANT_A)
        second = describe_chunk_index(root=index_root, index_name=INDEX_NAME)

        assert second["index_version"] > first["index_version"]
        manifest, pointer, _path = load_active_manifest(index_root, INDEX_NAME)
        assert int(manifest.index_version) == int(pointer.index_version)
