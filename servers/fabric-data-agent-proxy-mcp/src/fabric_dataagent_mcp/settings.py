"""Environment-supplied scope; identifiers and secrets are never committed."""

from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The two Fabric documents name different scopes for service-principal tokens; both are accepted here
# so that the probe can test each.
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FABRIC_PROXY_", extra="forbid")

    tenant_id: str
    workspace_id: str
    data_agent_id: str
    fabric_api_host: str = "api.fabric.microsoft.com"
    token_scope: Literal[
        "https://api.fabric.microsoft.com/.default",
        "https://analysis.windows.net/powerbi/api/.default",
    ] = FABRIC_SCOPE
    # azure_cli is for local checks as the signed-in developer; the deployed proxy uses client_secret.
    credential_mode: Literal["azure_cli", "client_secret"] = "client_secret"
    client_id: str | None = None
    client_secret: SecretStr | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    restricted_network_confirmed: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=300, ge=1, le=900)

    @property
    def mcp_url(self) -> str:
        return (
            f"https://{self.fabric_api_host}/v1/mcp/workspaces/{self.workspace_id}"
            f"/dataagents/{self.data_agent_id}/agent"
        )

    @model_validator(mode="after")
    def validate_scope(self) -> "Settings":
        for value in (self.tenant_id, self.workspace_id, self.data_agent_id):
            UUID(value)
        if not self.fabric_api_host.endswith(".fabric.microsoft.com"):
            raise ValueError("FABRIC_PROXY_FABRIC_API_HOST must be a Fabric API host")
        if self.credential_mode == "client_secret":
            if not self.client_id or not self.client_secret:
                raise ValueError("client_secret mode requires FABRIC_PROXY_CLIENT_ID and FABRIC_PROXY_CLIENT_SECRET")
            UUID(self.client_id)
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            if not self.restricted_network_confirmed or not self.allowed_hosts:
                raise ValueError("Non-loopback binding requires restricted-network confirmation and host allowlist")
        return self
