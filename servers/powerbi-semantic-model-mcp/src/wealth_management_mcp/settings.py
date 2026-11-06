"""Explicit POC scope; no implicit identity chains or tenant selection."""

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Deliberately unset. Real Fabric identifiers are supplied by the environment and are
#: never committed; live backends reject this value.
UNSET_ID = UUID("00000000-0000-0000-0000-000000000000")


class Settings(BaseSettings):
    """Read environment variables, not client-supplied configuration."""

    model_config = SettingsConfigDict(env_prefix="WEALTH_MCP_", extra="forbid")

    workspace_id: UUID = UNSET_ID
    semantic_model_id: UUID = UNSET_ID
    report_id: UUID = UNSET_ID
    tenant_id: str = ""
    catalog_path: Path = Path("artifacts/wealth-management-catalog.json")
    contract_path: Path | None = None
    backend: Literal["offline", "remote_mcp", "fabric_rest"] = "offline"
    # executeQueries rejects managed identities; executeDaxQueries accepts them but
    # returns Arrow and rejects the delegated Azure CLI token.
    dax_endpoint: Literal["execute_queries", "execute_dax_queries"] = "execute_dax_queries"
    credential_mode: Literal["azure_cli", "managed_identity"] = "azure_cli"
    managed_identity_client_id: str | None = None
    allow_application_identity: bool = False
    enable_generate_query: bool = False
    enable_effective_username_test: bool = False
    effective_username_allowlist: list[str] = Field(default_factory=list)
    allow_author_catalog: bool = False
    allow_unverified_live_definition: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    restricted_network_confirmed: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    max_rows: int = Field(default=1000, ge=1, le=100000)

    @model_validator(mode="after")
    def validate_scope(self) -> "Settings":
        normalized_usernames: list[str] = []
        seen_usernames: set[str] = set()
        for value in self.effective_username_allowlist:
            username = value.strip()
            normalized = username.casefold()
            if not username or "@" not in username:
                raise ValueError("Effective username allowlist entries must be tenant UPNs")
            if normalized in seen_usernames:
                raise ValueError("Effective username allowlist entries must be unique")
            seen_usernames.add(normalized)
            normalized_usernames.append(username)
        self.effective_username_allowlist = normalized_usernames
        if self.enable_effective_username_test:
            if self.backend != "fabric_rest":
                raise ValueError("Effective username testing requires the fabric_rest backend")
            if self.dax_endpoint != "execute_dax_queries":
                raise ValueError("Effective username testing requires executeDaxQueries")
            if not self.effective_username_allowlist:
                raise ValueError("Effective username testing requires a nonempty server-side allowlist")
        if self.backend == "remote_mcp" and (not self.tenant_id or not self.contract_path):
            raise ValueError("Live mode requires an explicit tenant and reviewed contract manifest")
        # REST mode owns its own tool contracts, so it needs a tenant but no Microsoft manifest.
        if self.backend == "fabric_rest" and not self.tenant_id:
            raise ValueError("REST mode requires an explicit tenant")
        if self.backend != "offline":
            unset = [
                name
                for name in ("workspace_id", "semantic_model_id", "report_id")
                if getattr(self, name) == UNSET_ID
            ]
            if unset:
                raise ValueError(
                    f"Set {', '.join(f'WEALTH_MCP_{name.upper()}' for name in unset)}; "
                    "Fabric identifiers are supplied by the environment, not committed defaults"
                )
        if self.tenant_id:
            UUID(self.tenant_id)
        if self.credential_mode == "managed_identity" and not self.allow_application_identity:
            raise ValueError("Application identity requires explicit approval; per-user RLS is not supported")
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            if not self.restricted_network_confirmed or not self.allowed_hosts:
                raise ValueError("Non-loopback binding requires restricted-network confirmation and host allowlist")
        return self