"""Command line: regenerate schemas, or join Foundry traces onto a stored run.

    python -m foundry_eval schemas <directory>
    python -m foundry_eval traces <runs-root> <run_id> [--app-id <id>]   (app id defaults to FOUNDRY_APPINSIGHTS_APP_ID)
"""

from __future__ import annotations

import argparse
import os
import sys

from .store import RunStore, write_json_schemas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="foundry_eval")
    commands = parser.add_subparsers(dest="command", required=True)
    schemas = commands.add_parser("schemas", help="write the JSON Schema files")
    schemas.add_argument("directory")
    traces = commands.add_parser("traces", help="join Foundry (Application Insights) traces onto a run")
    traces.add_argument("root")
    traces.add_argument("run_id")
    traces.add_argument("--app-id", default=os.environ.get("FOUNDRY_APPINSIGHTS_APP_ID"))
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "schemas":
        for path in write_json_schemas(args.directory):
            print(path)
        return 0

    if not args.app_id:
        parser.error("--app-id or FOUNDRY_APPINSIGHTS_APP_ID is required")
    from .traces import enrich_run

    store = RunStore.open(args.root, args.run_id)
    records, missing = enrich_run(store, args.app_id)
    print(f"{len(records)} attempts traced, {missing} without spans yet (retry in a few minutes)")
    for record in records:
        print(f"  {record.response_id} tool_error={record.tool_error} error_type={record.error_type}")
    print(f"run n_tool_errors={store.run.n_tool_errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
