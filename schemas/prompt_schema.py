from pydantic import BaseModel, Field


class PromptVersionPayload(BaseModel):
    version: str = ""
    system_prompt: str = Field(min_length=1)
    developer_prompt: str = ""
    change_reason: str = ""
    evaluation_result: str = ""
    effective_at: str = ""


class PromptVersionStatusRequest(BaseModel):
    status: str = Field(pattern="^(evaluation|approved|canary)$")
    evaluation_result: str = ""


class PromptVersionItem(BaseModel):
    id: int
    version: str
    status: str
    system_prompt: str
    developer_prompt: str
    change_reason: str
    author: str
    evaluation_result: str
    effective_at: str
    created_at: str
    activated_at: str
    rolled_back_from: str


class PromptVersionListResponse(BaseModel):
    count: int
    items: list[PromptVersionItem]
