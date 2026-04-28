import json
from pathlib import Path
from fastapi import HTTPException


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "takeout_customer_service_seed.jsonl"


def iter_examples():
    with DATA_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            yield json.loads(line)


def get_categories():
    categories = set()
    for item in iter_examples():
        categories.add(item["category"])

    return {
        "categories": sorted(categories),
        "count": len(categories),
    }


def get_examples_by_category(category: str, limit: int = 5):
    examples = []
    if category not in get_categories()["categories"]:
        raise HTTPException(status_code=404, detail="Category not found")

    for item in iter_examples():
        if item["category"] == category:
            examples.append(
                {
                    "question": item["question"],
                    "answer": item["answer"],
                }
            )
            if len(examples) >= limit:
                break

    return {
        "category": category,
        "count": len(examples),
        "examples": examples,
    }


def search_examples(keyword: str, limit: int = 5):
    results = []
    for item in iter_examples():
        if keyword in item["question"] or keyword in item["answer"]:
            results.append(
                {
                    "category": item["category"],
                    "question": item["question"],
                    "answer": item["answer"],
                }
            )
            if len(results) >= limit:
                break

    return {
        "keyword": keyword,
        "count": len(results),
        "results": results,
    }
