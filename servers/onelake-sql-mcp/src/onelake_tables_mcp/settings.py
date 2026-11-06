"""Environment-supplied scope; identifiers are never committed."""

import re
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ONELAKE_MCP_", extra="forbid")

    tenant_id: str
    sql_endpoint: str
    database: str
    allowed_schemas: list[str] = Field(default_factory=lambda: ["dbo"])
    # Optional "schema.table" entries; scopes what the schema tool advertises, not what SQL can reach.
    allowed_tables: list[str] = Field(default_factory=list)
    credential_mode: Literal["azure_cli", "managed_identity"] = "azure_cli"
    managed_identity_client_id: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    restricted_network_confirmed: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    max_rows: int = Field(default=1000, ge=1, le=100000)
    max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)

    @model_validator(mode="after")
    def validate_scope(self) -> "Settings":
        UUID(self.tenant_id)
        if not self.sql_endpoint.endswith(".fabric.microsoft.com"):
            raise ValueError("ONELAKE_MCP_SQL_ENDPOINT must be a Fabric SQL analytics endpoint host")
        if not self.database.strip():
            raise ValueError("ONELAKE_MCP_DATABASE is required")
        # Schemas are interpolated into the catalog query, so they must be plain identifiers.
        if not self.allowed_schemas or not all(_IDENTIFIER.match(s) for s in self.allowed_schemas):
            raise ValueError("ONELAKE_MCP_ALLOWED_SCHEMAS must be nonempty plain identifiers")
        for entry in self.allowed_tables:
            schema, _, table = entry.partition(".")
            if schema not in self.allowed_schemas or not _IDENTIFIER.match(table):
                raise ValueError("ONELAKE_MCP_ALLOWED_TABLES entries must be <allowed schema>.<table>")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            if not self.restricted_network_confirmed or not self.allowed_hosts:
                raise ValueError("Non-loopback binding requires restricted-network confirmation and host allowlist")
        return self
