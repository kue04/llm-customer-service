from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


def _get_env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    return Path(value)


@dataclass(frozen=True)
class RagConfig:
    embedding_model_name: str = os.getenv("RAG_EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5")
    reranker_model_name: str = os.getenv("RAG_RERANKER_MODEL_NAME", "BAAI/bge-reranker-base")
    generation_provider: str = os.getenv("RAG_GENERATION_PROVIDER", "local")  # "online" or "local"
    online_model_name: str = os.getenv("RAG_ONLINE_MODEL_NAME", "gpt-5.5")
    online_api_base_url: str = os.getenv("RAG_ONLINE_API_BASE_URL", "https://relayai.tech/v1")
    online_api_key_env: str = os.getenv("RAG_ONLINE_API_KEY_ENV", "OPENAI_API_KEY")
    model_rerank_weight: float = _get_env_float("RAG_MODEL_RERANK_WEIGHT", 0.01)
    min_vector_score: float = _get_env_float("RAG_MIN_VECTOR_SCORE", 0.40)
    faiss_store_dir: Path = _get_env_path("RAG_FAISS_STORE_DIR", PROJECT_ROOT / "data" / "faiss_store")
    answer_composer_enabled: bool = _get_env_bool("RAG_ANSWER_COMPOSER_ENABLED", True)
    reply_rules_enabled: bool = _get_env_bool("RAG_REPLY_RULES_ENABLED", True)

    @property
    def faiss_index_path(self) -> Path:
        return self.faiss_store_dir / "real_vector.index"

    @property
    def faiss_docs_path(self) -> Path:
        return self.faiss_store_dir / "real_vector_docs.json"


RAG_CONFIG = RagConfig()


def get_rag_config() -> RagConfig:
    return RAG_CONFIG


def get_rag_config_dict() -> dict:
    config = get_rag_config()
    data = asdict(config)
    data["faiss_store_dir"] = str(config.faiss_store_dir)
    data["faiss_index_path"] = str(config.faiss_index_path)
    data["faiss_docs_path"] = str(config.faiss_docs_path)
    return data
