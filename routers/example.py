from fastapi import APIRouter, Depends, Query
from schemas.example_schema import (
    CategoriesResponse,
    ExamplesByCategoryResponse,
    SearchExamplesRequest,
    SearchExamplesResponse,
)
from services.auth_service import get_operator_context, require_read_operation_role
from services.example_service import get_categories, get_examples_by_category, search_examples

router = APIRouter()


@router.get("/categories", response_model=CategoriesResponse)
def categories(operator_context: dict = Depends(get_operator_context)):
    require_read_operation_role("example_read", operator_context)
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
def examples(
    category: str,
    limit: int = Query(default=5, ge=1, le=20),
    operator_context: dict = Depends(get_operator_context),
):
    require_read_operation_role("example_read", operator_context)
    return get_examples_by_category(category, limit)


@router.post("/search", response_model=SearchExamplesResponse)
def search_examples_api(
    request: SearchExamplesRequest,
    operator_context: dict = Depends(get_operator_context),
):
    require_read_operation_role("example_read", operator_context)
    return search_examples(request.keyword, request.limit)
