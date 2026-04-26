# app/utils/retriever.py
from typing import List


knowledge_base = [
    {"question": "What is your return policy?", "answer": "You can return products within 30 days."},
    {"question": "How can I contact customer support?", "answer": "You can contact support at support@example.com."},
]

STOP_WORDS = {"a", "an", "and", "are", "can", "how", "i", "is", "of", "the", "to", "what", "your"}


def normalize_terms(text: str) -> set[str]:
    terms = {term.strip("?.!,;:").lower() for term in text.split()}
    return {term for term in terms if term and term not in STOP_WORDS}


def retrieve_documents(query: str) -> List[str]:
    query_terms = normalize_terms(query)

    results = []
    for item in knowledge_base:
        question = item["question"].lower()
        question_terms = normalize_terms(question)
        if query.lower() in question or query_terms.intersection(question_terms):
            results.append(item["answer"])
    return results
