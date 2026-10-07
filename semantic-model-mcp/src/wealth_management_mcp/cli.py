"""Generate/validate offline metadata, discover real tools, and serve the POC."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import uvicorn
from mcp.server.stdio import stdio_server

from .backend import BackendError, RemoteBackend
from .contracts import discovery_manifest, write_json
from .logging_config import configure_logging
from .profile import build_catalog, load_profile_schema, validate_catalog
from .server import create_application
from .settings import Settings


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Generate the private Fabric/DAX catalog from inspection JSON")
    export.add_argument("--metadata", type=Path, required=True)
    export.add_argument("--summary", type=Path, required=True)
    export.add_argument("--overlay", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--model-id", help="Bind the catalog to a specific semantic model ID")
    export.add_argument("--workspace-id", help="Bind the catalog to a specific workspace ID")
    export.add_argument("--report-id", help="Bind the catalog to a specific report ID")
    validate = commands.add_parser("validate", help="Validate catalog structure, semantics and integrity")
    validate.add_argument("catalog", type=Path)
    schema = commands.add_parser("export-schema", help="Export the separately identified derivative schema")
    schema.add_argument("--output", type=Path, required=True)
    discover = commands.add_parser("discover", help="Read real Microsoft tools; does not execute data queries")
    discover.add_argument("--tenant-id", required=True)
    discover.add_argument("--output", type=Path, required=True)
    serve = commands.add_parser("serve", help="Serve the POC over stdio or Streamable HTTP")
    serve.add_argument("--catalog", type=Path, required=True)
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--port", type=int)
    status = commands.add_parser("status", help="Show local mode/catalog status; no network access")
    status.add_argument("--catalog", type=Path, required=True)
    return result


async def run_stdio(settings: Settings) -> None:
    server = create_application(settings).server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    arguments = parser().parse_args()
    # Libraries must not print Azure identity exception payloads/token details.
    configure_logging()
    try:
        if arguments.command == "export":
            binding = {
                key: value
                for key, value in (
                    ("model_id", arguments.model_id),
                    ("workspace_id", arguments.workspace_id),
                    ("report_id", arguments.report_id),
                )
                if value
            }
            catalog = build_catalog(arguments.metadata, arguments.summary, arguments.overlay, **binding)
            write_json(arguments.output, catalog)
            print(json.dumps({"catalog": str(arguments.output), "coverage": catalog["coverage"]}, indent=2))
        elif arguments.command == "validate":
            catalog = json.loads(arguments.catalog.read_text(encoding="utf-8"))
            validate_catalog(catalog)
            print(json.dumps({"valid": True, "catalog_sha256": catalog["catalog_sha256"], "coverage": catalog["coverage"]}, indent=2))
        elif arguments.command == "export-schema":
            write_json(arguments.output, load_profile_schema())
            print("Profile schema exported; this is not the unmodified upstream OSSIE schema.")
        elif arguments.command == "discover":
            settings = Settings(tenant_id=arguments.tenant_id, backend="offline")
            discovery = asyncio.run(RemoteBackend(settings).discover())
            write_json(arguments.output, discovery_manifest(discovery))
            print(json.dumps({"status": "unreviewed", "tools": [x["name"] for x in discovery["tools"]]}, indent=2))
        elif arguments.command == "status":
            print(json.dumps(create_application(Settings(catalog_path=arguments.catalog)).status(), indent=2))
        elif arguments.command == "serve":
            overrides: dict = {"catalog_path": arguments.catalog}
            if arguments.port is not None:
                overrides["port"] = arguments.port
            settings = Settings(**overrides)
            if arguments.transport == "stdio":
                asyncio.run(run_stdio(settings))
            else:
                app = create_application(settings).http_app()
                uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning", access_log=False)
    except BackendError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
    except (OSError, ValueError):
        print("Configuration or artifact validation failed. Check local paths, approved settings and catalog integrity.", file=sys.stderr)
        raise SystemExit(2) from None


def metadata_main() -> None:
    main()