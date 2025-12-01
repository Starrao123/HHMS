from typing import Dict, Optional

from pydantic import BaseModel, Field


class Dependency(BaseModel):
    status: str
    response_time_ms: Optional[int] = None


class HealthCheckResponse(BaseModel):
    service: str
    status: str
    dependencies: Dict[str, Dependency] = Field(default_factory=dict)
