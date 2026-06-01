from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RagConfig:
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    reranker_model_name: str = "BAAI/bge-reranker-base"
    generation_provider: str = "local"  # "online" or "local"
    online_model_name: str = "gpt-5.5"
    online_api_base_url: str = "https://relayai.tech/v1"
    online_api_key_env: str = "OPENAI_API_KEY"
    model_rerank_weight: float = 0.01
    min_vector_score: float = 0.40
    faiss_store_dir: Path = PROJECT_ROOT / "data" / "faiss_store"
    answer_composer_enabled: bool = True
    reply_rules_enabled: bool = True

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
