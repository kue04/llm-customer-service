from pydantic import BaseModel, Field


class CategoriesResponse(BaseModel):
    categories: list[str]
    count: int


class ExampleItem(BaseModel):
    question: str
    answer: str


class ExamplesByCategoryResponse(BaseModel):
    category: str
    count: int
    examples: list[ExampleItem]


class SearchExamplesRequest(BaseModel):
    keyword: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class SearchResultItem(BaseModel):
    category: str
    question: str
    answer: str


class SearchExamplesResponse(BaseModel):
    keyword: str
    count: int
    results: list[SearchResultItem]
