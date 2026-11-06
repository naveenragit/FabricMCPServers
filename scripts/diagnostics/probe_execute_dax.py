"""Probe executeDaxQueries and decode the Arrow IPC response.

Query errors are returned as HTTP 200 with an error rowset, so the stream metadata must be
inspected rather than trusting the status code.
"""

from __future__ import annotations

import io
import os

import httpx2
import pyarrow as pa
from azure.identity import AzureCliCredential

WORKSPACE_ID = os.environ["WEALTH_MCP_WORKSPACE_ID"]
MODEL_ID = os.environ["WEALTH_MCP_SEMANTIC_MODEL_ID"]
SCOPE = "https://analysis.windows.net/powerbi/api/.default"
QUERY = 'EVALUATE ROW("Tables", COUNTROWS(INFO.VIEW.TABLES()))'


def main() -> None:
    token = AzureCliCredential().get_token(SCOPE).token
    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"
        f"/datasets/{MODEL_ID}/executeDaxQueries"
    )
    response = httpx2.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": QUERY},
        timeout=60,
    )
    print("http_status:", response.status_code)
    print("content_type:", response.headers.get("content-type"))
    print("bytes:", len(response.content))
    if response.status_code >= 400:
        print("error:", response.text[:400])
        return

    stream = io.BytesIO(response.content)
    while stream.tell() < len(response.content):
        reader = pa.ipc.open_stream(stream)
        table = reader.read_all()
        metadata = {k.decode(): v.decode() for k, v in (reader.schema.metadata or {}).items()}
        print("\nrowset metadata:", metadata)
        print("is_error:", metadata.get("IsError"))
        print("columns:", table.column_names)
        print("rows:", table.num_rows)
        print("data:", table.to_pylist()[:5])


main()
