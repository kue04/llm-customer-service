from pydantic import BaseModel


class ModelInfoResponse(BaseModel):
    generation_provider: str = ""
    online_model_name: str = ""
    online_api_base_url_configured: bool = False
    online_api_key_env: str = ""
    base_model: str
    adapter_enabled: bool
    adapter_name: str | None
