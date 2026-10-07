import pytest

from onelake_tables_mcp.sql_backend import BackendError, validated_select


@pytest.mark.parametrize("query", [
    "SELECT TOP 10 * FROM dbo.dim_client",
    "with x as (select 1 as a) select a from x;",
    "SELECT [update], update_date, 'drop table x' AS note FROM dbo.t -- delete",
    "SELECT COUNT(*) FROM dbo.fact_transaction /* insert */ WHERE settled = 1",
])
def test_accepts_read_only(query: str) -> None:
    assert validated_select(query)


@pytest.mark.parametrize("query", [
    "",
    "DROP TABLE dbo.t",
    "SELECT 1; DROP TABLE dbo.t",
    "SELECT 1 DELETE FROM dbo.t",
    "SELECT * INTO dbo.copy FROM dbo.t",
    "SELECT * FROM OPENROWSET(BULK 'https://x/y.parquet') AS r",
    "WITH x AS (SELECT 1 a) SELECT a FROM x EXEC sp_who",
    "EXEC('SELECT 1')",
])
def test_rejects_writes_and_escapes(query: str) -> None:
    with pytest.raises(BackendError):
        validated_select(query)
