"""T-SQL over TDS to the Lakehouse SQL analytics endpoint with one fixed backend identity."""

from __future__ import annotations

import asyncio
import re
import struct
from contextlib import closing
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from azure.identity import AzureCliCredential, ManagedIdentityCredential

from .settings import Settings

SQL_SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256


class BackendError(Exception):
    """Safe-to-return message; never carries upstream payloads."""


# Strip comments, string literals and quoted identifiers before keyword checks so that
# data values or names like [update] cannot trip, and hidden statements cannot hide.
_NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\[(?:[^\]]|\]\])*\]|\"(?:[^\"]|\"\")*\"", re.S)
_LEADING = re.compile(r"^\s*(select|with)\b", re.I)
# T-SQL allows statements without semicolons, so a write keyword anywhere is rejected.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|create|alter|drop|truncate|exec|execute|sp_executesql|grant|revoke|"
    r"deny|into|openrowset|opendatasource|openquery|bulk|backup|restore|kill|shutdown|dbcc|use|"
    r"declare|set|waitfor|reconfigure)\b",
    re.I,
)


def validated_select(query: str) -> str:
    text = query.strip().rstrip(";").strip()
    if not text:
        raise BackendError("SQL query must not be blank")
    stripped = _NOISE.sub(" ", text)
    if not _LEADING.match(stripped):
        raise BackendError("Only read-only queries starting with SELECT or WITH are accepted")
    if ";" in stripped:
        raise BackendError("Only a single statement is accepted")
    keyword = _FORBIDDEN.search(stripped)
    if keyword:
        raise BackendError(f"Keyword '{keyword.group(1).upper()}' is not allowed in a read-only query")
    return text


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


class SqlBackend:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.credential_mode == "azure_cli":
            self._credential: Any = AzureCliCredential(tenant_id=settings.tenant_id, process_timeout=20)
        else:
            self._credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)

    def _connect(self) -> Any:
        import mssql_python

        try:
            token = self._credential.get_token(SQL_SCOPE).token.encode("utf-16-le")
        except Exception:
            raise BackendError("Backend sign-in is unavailable; check the configured identity") from None
        token_struct = struct.pack(f"<I{len(token)}s", len(token), token)
        # A cold SQL analytics endpoint can exceed the driver's ~15s default login timeout.
        for attempt in range(2):
            try:
                return mssql_python.connect(
                    f"Server={self.settings.sql_endpoint},1433;Database={self.settings.database};"
                    "Encrypt=yes;TrustServerCertificate=no;",
                    attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct},
                    timeout=30,
                )
            except mssql_python.OperationalError:
                if attempt == 0:
                    continue
                raise BackendError("Could not connect to the SQL analytics endpoint (timeout)") from None
            except Exception as exc:
                raise BackendError(f"Could not connect to the SQL analytics endpoint ({type(exc).__name__})") from None
        raise AssertionError("unreachable")

    def _fetch(self, query: str, limit: int | None) -> dict[str, Any]:
        with closing(self._connect()) as connection, closing(connection.cursor()) as cursor:
            try:
                cursor.execute(query)
            except Exception as exc:
                # Parity with the DAX server, which returns only a fault code and no engine text.
                raise BackendError(f"SQL query failed ({type(exc).__name__})") from None
            if cursor.description is None:
                return {"columns": [], "rows": []}
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall() if limit is None else cursor.fetchmany(limit + 1)
            if limit is not None and len(rows) > limit:
                raise BackendError(
                    f"Result exceeds the {limit}-row limit; aggregate, filter, or use TOP to reduce it"
                )
            return {"columns": columns, "rows": [[_json_safe(value) for value in row] for row in rows]}

    async def execute_sql(self, query: str) -> dict[str, Any]:
        sql = validated_select(query)
        async with asyncio.timeout(self.settings.timeout_seconds):
            return await asyncio.to_thread(self._fetch, sql, self.settings.max_rows)

    async def database_schema(self) -> dict[str, Any]:
        schemas = ", ".join(f"'{name}'" for name in self.settings.allowed_schemas)
        query = (
            "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, t.TABLE_TYPE, c.COLUMN_NAME, c.DATA_TYPE, c.IS_NULLABLE "
            "FROM INFORMATION_SCHEMA.COLUMNS c JOIN INFORMATION_SCHEMA.TABLES t "
            "ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
            f"WHERE c.TABLE_SCHEMA IN ({schemas}) "
            "ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION"
        )
        async with asyncio.timeout(self.settings.timeout_seconds):
            result = await asyncio.to_thread(self._fetch, query, None)
        allowed = {entry.casefold() for entry in self.settings.allowed_tables}
        tables: dict[tuple[str, str], dict[str, Any]] = {}
        for schema, table, table_type, column, data_type, nullable in result["rows"]:
            if allowed and f"{schema}.{table}".casefold() not in allowed:
                continue
            entry = tables.setdefault(
                (schema, table),
                {"schema": schema, "name": table, "type": table_type, "columns": []},
            )
            entry["columns"].append({"name": column, "type": data_type, "nullable": nullable == "YES"})
        return {"database": self.settings.database, "tables": list(tables.values())}
