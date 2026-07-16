from dataclasses import dataclass
import string

import jieba
from rank_bm25 import BM25Okapi

from utils.retriever import DOMAIN_KEYWORDS


PUNCTUATION = set(string.punctuation + "，。！？；：、（）《》“”‘’")
SINGLE_CHAR_BUSINESS_TERMS = ("券", "餐", "钱")


@dataclass(frozen=True)
class LexicalHit:
    doc_index: int
    score: float
    rank: int


def tokenize_for_bm25(text: str) -> list[str]:
    normalized = " ".join(str(text or "").split())
    tokens = [
        token.strip()
        for token in jieba.lcut(normalized)
        if token.strip() and not all(char in PUNCTUATION for char in token)
    ]
    for keyword in DOMAIN_KEYWORDS:
        if keyword in normalized and keyword not in tokens:
            tokens.append(keyword)
    for term in SINGLE_CHAR_BUSINESS_TERMS:
        if term in normalized and term not in tokens:
            tokens.append(term)
    return tokens


class LexicalRetriever:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.tokenized_corpus = [
            tokenize_for_bm25(document.get("text", ""))
            for document in documents
        ]
        self.index = BM25Okapi(self.tokenized_corpus)

    def search(self, query: str, top_k: int) -> list[LexicalHit]:
        scores = self.index.get_scores(tokenize_for_bm25(query))
        ranked = sorted(
            enumerate(scores),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        return [
            LexicalHit(
                doc_index=int(doc_index),
                score=float(score),
                rank=rank,
            )
            for rank, (doc_index, score) in enumerate(ranked[:top_k], start=1)
            if float(score) > 0.0
        ]
