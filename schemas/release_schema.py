from pydantic import BaseModel


class ReleaseChecklistItem(BaseModel):
    name: str
    status: str
    evidence: str
    next_step: str = ""


class ReleaseChecklistResponse(BaseModel):
    ready: bool
    failed_count: int
    warning_count: int
    items: list[ReleaseChecklistItem]
