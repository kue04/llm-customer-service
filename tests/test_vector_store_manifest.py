from utils.vector_store_manifest import (
    PREPROCESSING_VERSION,
    build_vector_store_manifest,
    document_fingerprint,
    manifest_is_compatible,
)


DOCUMENTS = [
    {
        "source": {
            "id": "kb_1_v1",
            "base_id": "kb_1",
            "version": "v1",
            "title": "退款进度",
            "question": "多久到账",
            "answer": "查看订单详情",
            "category": "退款",
            "intent": "退款进度",
            "source": "knowledge_ops",
            "updated_at": "2026-07-16T10:00:00Z",
            "effective_at": "",
            "expired_at": "",
        }
    }
]


def test_document_fingerprint_changes_when_knowledge_changes():
    changed = [{"source": {**DOCUMENTS[0]["source"], "answer": "新的标准回答"}}]
    assert document_fingerprint(DOCUMENTS) != document_fingerprint(changed)


def test_manifest_rejects_different_embedding_model():
    manifest = build_vector_store_manifest(DOCUMENTS, "model-a", dimension=512)
    assert manifest_is_compatible(manifest, DOCUMENTS, "model-a")
    assert not manifest_is_compatible(manifest, DOCUMENTS, "model-b")


def test_manifest_records_preprocessing_dimension_and_document_count():
    manifest = build_vector_store_manifest(DOCUMENTS, "model-a", dimension=512)
    assert manifest["preprocessing_version"] == PREPROCESSING_VERSION
    assert manifest["dimension"] == 512
    assert manifest["document_count"] == 1
