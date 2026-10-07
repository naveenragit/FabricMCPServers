"""Reviewed Microsoft wire contracts, kept separate from Python handler names."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from mcp.types import Tool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from referencing import Registry

from .backend import ENDPOINT
from .profile import canonical_sha256

Role = Literal["get_semantic_model_schema", "execute_query", "get_report_metadata", "generate_query"]


class RoleBinding(BaseModel):
    """Paths contain exact JSON object keys, not guessed or normalized aliases."""

    model_config = ConfigDict(extra="forbid")
    role: Role
    tool_name: str
    model_id_path: list[str] | None = None
    report_id_path: list[str] | None = None
    dax_query_path: list[str] | None = None
    question_path: list[str] | None = None
    schema_context_path: list[str] | None = None
    response_policy_reviewed: bool = False
    response_error_paths: list[list[str]] = Field(default_factory=list)
    response_success_path: list[str] | None = None
    response_success_value: Any = True
    response_rows_paths: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_paths(self) -> RoleBinding:
        required = [self.report_id_path] if self.role == "get_report_metadata" else [self.model_id_path]
        if self.role == "execute_query":
            required.append(self.dax_query_path)
        if self.role == "generate_query":
            required.extend([self.question_path, self.schema_context_path])
        if any(not path or any(not part for part in path) for path in required):
            raise ValueError("Reviewed role needs explicit paths for its required conceptual inputs")
        return self


class ContractManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["1.0.0"] = "1.0.0"
    endpoint: str = ENDPOINT
    status: Literal["unreviewed", "reviewed"]
    protocol_version: str
    tools: list[dict[str, Any]]
    discovery_sha256: str
    roles: list[RoleBinding]

    def verified_tools(self) -> dict[str, Tool]:
        if self.endpoint != ENDPOINT or self.status != "reviewed":
            raise ValueError("Live server requires a reviewed manifest for the fixed Microsoft endpoint")
        if canonical_sha256(self.tools) != self.discovery_sha256:
            raise ValueError("Discovery contract hash mismatch")
        tools = {raw["name"]: Tool.model_validate(raw) for raw in self.tools}
        if len(tools) != len(self.tools):
            raise ValueError("Duplicate discovered tool names")
        if len({role.role for role in self.roles}) != len(self.roles):
            raise ValueError("Duplicate capability roles")
        if len({role.tool_name for role in self.roles}) != len(self.roles):
            raise ValueError("One wire tool cannot implement multiple approved roles")
        for role in self.roles:
            if not role.response_policy_reviewed:
                raise ValueError("Native success/error/result semantics require explicit review")
            if role.role == "get_semantic_model_schema" and not role.response_success_path:
                raise ValueError("Schema access requires a positively identified native success field")
            if role.role == "execute_query" and not role.response_rows_paths:
                raise ValueError("Query row locations must be reviewed before enabling execution")
            if role.tool_name not in tools:
                raise ValueError("Role references a tool absent from discovery")
            schema = tools[role.tool_name].input_schema
            local_validator(schema)
            for path in (role.model_id_path, role.report_id_path, role.dax_query_path,
                         role.question_path, role.schema_context_path):
                if path:
                    node = schema
                    for part in path:
                        # Fail closed on complex refs/composition: review a dedicated adapter,
                        # not silently assume a path through an unknown contract shape.
                        node = node.get("properties", {}).get(part)
                        if not isinstance(node, dict):
                            raise ValueError("Argument mapping must refer to explicit object properties")
            output_schema = tools[role.tool_name].output_schema
            if output_schema is not None:
                local_validator(output_schema)
        return tools


def local_validator(schema: dict[str, Any]) -> Draft202012Validator:
    def check(node: Any) -> None:
        if isinstance(node, dict):
            if "$schema" in node and node["$schema"] != "https://json-schema.org/draft/2020-12/schema":
                raise ValueError("Only the reviewed JSON Schema Draft 2020-12 dialect is supported")
            for key, value in node.items():
                if key in {"$ref", "$dynamicRef"} and not str(value).startswith("#"):
                    raise ValueError("Remote schema references are disabled")
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(schema)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=Registry())


def load_manifest(path: Path) -> ContractManifest:
    manifest = ContractManifest.model_validate_json(path.read_bytes())
    manifest.verified_tools()
    return manifest


def argument_value(arguments: dict[str, Any], path: list[str] | None) -> Any:
    value: Any = arguments
    if not path:
        return None
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError("Required bound argument is missing")
        value = value[key]
    return value


def discovery_manifest(discovery: dict[str, Any]) -> dict[str, Any]:
    """Produce an unreviewed manifest, never invent role identifiers or signatures."""
    return {
        "version": "1.0.0", "endpoint": ENDPOINT, "status": "unreviewed",
        "protocol_version": discovery["protocol_version"], "tools": discovery["tools"],
        "discovery_sha256": canonical_sha256(discovery["tools"]), "roles": [],
    }


def write_json(path: Path, value: Any) -> None:
    """Write a generated artifact atomically; never overwrite the PBIX."""
    if path.suffix.lower() != ".json":
        raise ValueError("Generated artifact output must have a .json suffix")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)