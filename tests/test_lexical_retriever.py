import pytest

from utils.lexical_retriever import LexicalRetriever, tokenize_for_bm25
from utils.vector_retriever import load_vector_documents


def test_tokenizer_keeps_single_character_business_words():
    assert "券" in tokenize_for_bm25("这张券不能用")
    assert "钱" in tokenize_for_bm25("钱多久到账")


@pytest.mark.parametrize(
    ("query", "expected_intent"),
    [
        ("退款多久到账", "退款进度"),
        ("骑手联系不上", "配送异常追问"),
        ("身份证信息", "隐私保护咨询"),
        ("优惠券不能用", "优惠券不可用"),
        ("餐里有异物", "食品安全投诉"),
    ],
)
def test_bm25_top3_contains_expected_intent(query, expected_intent):
    documents = load_vector_documents()
    hits = LexicalRetriever(documents).search(query, top_k=3)
    intents = {documents[hit.doc_index]["source"].get("intent") for hit in hits}
    assert expected_intent in intents
