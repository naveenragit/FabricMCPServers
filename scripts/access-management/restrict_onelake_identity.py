"""DENY the OneLake tables MCP identity SELECT on every object outside its 15-table scope.

Workspace Viewer grants ReadData on the whole SQL analytics endpoint; these DENYs narrow it.
Runs as the signed-in az CLI user (needs workspace Admin). Pass --revoke to undo.
"""

import argparse
import os
import struct
import subprocess
from contextlib import closing

import mssql_python

ALLOWED = {
    "EXTERNAL_wm.dim_account", "EXTERNAL_wm.dim_date", "EXTERNAL_wm.dim_security",
    "EXTERNAL_wm.fact_alert", "EXTERNAL_wm.fact_client_interaction",
    "EXTERNAL_wm.fact_opportunity", "EXTERNAL_wm.fact_recommendation",
    "EXTERNAL_gold.dim_client", "EXTERNAL_gold.dim_advisor_primary", "EXTERNAL_gold.dim_advisor_secondary",
    "EXTERNAL_silver.fact_aum_daily", "EXTERNAL_silver.fact_holding_snapshot",
    "EXTERNAL_silver.fact_lifecycle_event", "EXTERNAL_silver.fact_performance_daily",
    "EXTERNAL_silver.fact_transaction",
}
SCHEMAS_WITH_ALLOWED = {name.split(".")[0] for name in ALLOWED}
# System schemas stay readable for metadata; queryinsights holds other users' query text.
SKIP_SCHEMAS = {"sys", "INFORMATION_SCHEMA"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--principal", default=os.environ.get("ONELAKE_MCP_PRINCIPAL"), required=False,
                        help="Managed identity display name (the web app name); or set ONELAKE_MCP_PRINCIPAL")
    parser.add_argument("--revoke", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.principal:
        parser.error("--principal or ONELAKE_MCP_PRINCIPAL is required")

    token = subprocess.run(
        "az account get-access-token --resource https://database.windows.net/ --query accessToken -o tsv",
        capture_output=True, text=True, shell=True, check=True,
    ).stdout.strip().encode("utf-16-le")
    connection = mssql_python.connect(
        f"Server={os.environ['ONELAKE_SQL_ENDPOINT']},1433;Database={os.environ.get('ONELAKE_DATABASE', 'Wealth_Management')};"
        "Encrypt=yes;TrustServerCertificate=no;",
        attrs_before={1256: struct.pack(f"<I{len(token)}s", len(token), token)},
        timeout=60,
    )
    verb = "REVOKE SELECT ON {} FROM [{}]" if args.revoke else "DENY SELECT ON {} TO [{}]"
    with closing(connection), closing(connection.cursor()) as cursor:
        cursor.execute("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES")
        objects = [tuple(row) for row in cursor.fetchall()]
        statements = []
        # Whole-schema DENY also covers objects added later.
        for schema in sorted({row[0] for row in objects} - SCHEMAS_WITH_ALLOWED - SKIP_SCHEMAS):
            statements.append(verb.format(f"SCHEMA::[{schema}]", args.principal))
        for schema, table in sorted(objects):
            if schema in SCHEMAS_WITH_ALLOWED and f"{schema}.{table}" not in ALLOWED:
                statements.append(verb.format(f"OBJECT::[{schema}].[{table}]", args.principal))
        for statement in statements:
            print(statement)
            if not args.dry_run:
                cursor.execute(statement)
        if not args.dry_run:
            connection.commit()
    print(f"{len(statements)} statements {'listed' if args.dry_run else 'applied'}")


if __name__ == "__main__":
    main()
