"""Determine whether the deployed semantic model defines RLS roles.

Service principals cannot use Execute Queries against an RLS-enabled model regardless of
tenant settings, and INFO.ROLES() is blocked through that API, so use the admin scanner.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx2
from azure.identity import AzureCliCredential

WORKSPACE_ID = os.environ["WEALTH_MCP_WORKSPACE_ID"]
BASE = "https://api.powerbi.com/v1.0/myorg/admin/workspaces"
SCOPE = "https://analysis.windows.net/powerbi/api/.default"


def main() -> None:
    token = AzureCliCredential().get_token(SCOPE).token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    with httpx2.Client(timeout=60) as client:
        start = client.post(
            f"{BASE}/getInfo",
            params={"datasetSchema": "true", "datasetExpressions": "true"},
            headers=headers,
            json={"workspaces": [WORKSPACE_ID]},
        )
        if start.status_code >= 400:
            print(f"getInfo failed: {start.status_code} {start.text[:400]}")
            sys.exit(1)
        scan_id = start.json()["id"]
        print("scan id:", scan_id)

        for _ in range(30):
            status = client.get(f"{BASE}/scanStatus/{scan_id}", headers=headers).json()
            if status.get("status") == "Succeeded":
                break
            time.sleep(2)
        else:
            print("scan did not complete")
            sys.exit(1)

        result = client.get(f"{BASE}/scanResult/{scan_id}", headers=headers).json()

    for workspace in result.get("workspaces", []):
        for dataset in workspace.get("datasets", []):
            roles = dataset.get("roles", [])
            print(f"\ndataset: {dataset.get('name')} ({dataset.get('id')})")
            print(f"  RLS roles: {len(roles)}")
            for role in roles:
                members = role.get("members", [])
                tables = [t.get("name") for t in role.get("tablePermissions", [])]
                print(f"    - {role.get('name')} | members={len(members)} | tables={tables}")
            for key in ("targetStorageMode", "contentProviderType"):
                if dataset.get(key):
                    print(f"  {key}: {dataset[key]}")

    with open("artifacts/scan-result.json", "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print("\nfull scan written to artifacts/scan-result.json")


main()
