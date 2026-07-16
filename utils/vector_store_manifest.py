from datetime import datetime, timezone
import hashlib
import json


SCHEMA_VERSION = 1
PREPROCESSING_VERSION = "faq-v2"
FINGERPRINT_FIELDS = (
    "id",
    "base_id",
    "version",
    "title",
    "question",
    "answer",
    "category",
    "intent",
    "source",
    "updated_at",
    "effective_at",
    "expired_at",
)


def document_fingerprint(documents: list[dict]) -> str:
    payload = []
    for document in documents:
        source = document.get("source", document)
        payload.append({field: source.get(field) for field in FINGERPRINT_FIELDS})
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_vector_store_manifest(
    documents: list[dict],
    embedding_model_name: str,
    dimension: int,
    created_at: str | None = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "embedding_model_name": embedding_model_name,
        "preprocessing_version": PREPROCESSING_VERSION,
        "document_fingerprint": document_fingerprint(documents),
        "dimension": dimension,
        "document_count": len(documents),
        "created_at": created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def manifest_is_compatible(
    manifest: dict,
    documents: list[dict],
    embedding_model_name: str,
    expected_dimension: int | None = None,
) -> bool:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return False
    if manifest.get("preprocessing_version") != PREPROCESSING_VERSION:
        return False
    if manifest.get("embedding_model_name") != embedding_model_name:
        return False
    if manifest.get("document_count") != len(documents):
        return False
    if manifest.get("document_fingerprint") != document_fingerprint(documents):
        return False
    if expected_dimension is not None and manifest.get("dimension") != expected_dimension:
        return False
    return True
