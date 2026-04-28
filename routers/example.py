from fastapi import APIRouter, Query
from schemas.example_schema import (
    CategoriesResponse,
    ExamplesByCategoryResponse,
    SearchExamplesRequest,
    SearchExamplesResponse,
)
from services.example_service import get_categories, get_examples_by_category, search_examples

router = APIRouter()


@router.get("/categories", response_model=CategoriesResponse)
def categories():
    return get_categories()


@router.get(
    "/by-category",
    response_model=ExamplesByCategoryResponse,
    responses={
        404: {
            "description": "Category not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Category not found"},
                },
            },
        },
    },
)
def examples(category: str, limit: int = Query(default=5, ge=1, le=20)):
    return get_examples_by_category(category, limit)


@router.post("/search", response_model=SearchExamplesResponse)
def search_examples_api(request: SearchExamplesRequest):
    return search_examples(request.keyword, request.limit)
