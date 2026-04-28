from pydantic import BaseModel

class ModelInfoResponse(BaseModel):
    base_model: str
    adapter_enabled: bool
    adapter_name: str | None