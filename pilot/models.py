from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field

Name = Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")]

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Login(Strict):
    email: str = Field(max_length=254)
    password: str = Field(max_length=512)

class Policy(Strict):
    window: int = Field(default=60, ge=30, le=300)
    minimum_requests: int = Field(default=100, ge=1, le=1000000)
    max_error_rate: float = Field(default=.05, gt=0, le=1)
    max_p95_seconds: float = Field(default=.3, gt=0, le=60)
    rollout_timeout: int = Field(default=180, ge=30, le=1800)

class Service(Strict):
    name: Name
    environment: Name = "development"
    namespace: Name = "releasepilot-demo"
    deployment: Name
    container: Name = "api"
    repository: str = Field(pattern=r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-][a-zA-Z0-9_.-]*$", max_length=200)
    approval: bool = True
    policy: Policy = Field(default_factory=Policy)

class ImportRun(Strict):
    run_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")

class Release(Strict):
    service_id: int = Field(gt=0)
    artifact_id: int = Field(gt=0)
    request_key: str = Field(min_length=8, max_length=100)

class Reason(Strict):
    reason: str = Field(min_length=1, max_length=1000)
