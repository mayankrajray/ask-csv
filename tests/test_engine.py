import pytest

from app.engine import DataEngine, validate_readonly
from app.anomalies import analyse_dataframe

import pandas as pd
from pathlib import Path

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sales.csv"


# ------------------------------------------------------------------ guard
@pytest.mark.parametrize("q", [
    "SELECT 1",
    "  select * from sales limit 5  ",
    "WITH t AS (SELECT 1) SELECT * FROM t",
    "DESCRIBE sales",
    "EXPLAIN SELECT region FROM sales",
])
def test_guard_accepts_reads(q):
    assert validate_readonly(q)


@pytest.mark.parametrize("q", [
    "",
    "DROP TABLE sales",
    "delete from sales",
    "UPDATE sales SET revenue = 0",
    "INSERT INTO sales VALUES (1)",
    "CREATE TABLE t (x INT)",
    "SELECT 1; DROP TABLE sales",      # multi-statement
    "ATTACH 'x.db' AS x",
    "-- comment\nDROP TABLE sales",    # comment-obscured write
    "COPY sales TO 'out.csv'",
])
def test_guard_rejects_writes(q):
    with pytest.raises(ValueError):
        validate_readonly(q)


# ------------------------------------------------------------------ engine
def test_engine_loads_sample():
    e = DataEngine()
    e.load_csv("sales", str(SAMPLE))
    assert e.tables == ["sales"]
    cols = e.schema("sales")[0]["columns"]
    assert any(c["name"] == "revenue" for c in cols)
    prof = e.profile("sales")[0]
    assert prof["rows"] > 480
    df = e.execute("SELECT region, SUM(revenue) AS total FROM sales GROUP BY 1 ORDER BY 2 DESC")
    assert list(df.columns) == ["region", "total"]
    assert len(df) == 4  # four planted regions


# ------------------------------------------------------------------ anomalies
def test_planted_anomalies_detected():
    df = pd.read_csv(SAMPLE)
    res = analyse_dataframe(df, method="iqr")
    rev = next(c for c in res["columns"] if c["column"] == "revenue")
    assert rev["count"] >= 7          # we planted 7 extreme spikes
    assert rev["lower_bound"] < rev["upper_bound"]
    assert "sample" in rev


def test_zscore_method():
    df = pd.read_csv(SAMPLE)
    res = analyse_dataframe(df, method="zscore")
    rev = next(c for c in res["columns"] if c["column"] == "revenue")
    # z-score finds fewer than IQR here because the spikes themselves inflate σ
    assert rev["count"] >= 3


def test_sanitizer_casts_date_trunc():
    from app.tools import _sanitize_duckdb_sql
    q = _sanitize_duckdb_sql("SELECT date_trunc('month', order_date) FROM sales")
    assert "TRY_CAST(order_date AS DATE)" in q
    # already-cast input is left alone
    ok = "SELECT date_trunc('month', CAST(order_date AS DATE)) FROM sales"
    assert _sanitize_duckdb_sql(ok) == ok


@pytest.mark.parametrize("q", [
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_text('/etc/hostname')",
    "SELECT * FROM glob('/*')",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM parquet_scan('x.parquet')",
])
def test_guard_rejects_file_readers(q):
    with pytest.raises(ValueError):
        validate_readonly(q)


def test_guard_ignores_keywords_inside_literals():
    assert validate_readonly("SELECT * FROM sales WHERE product = 'replace set load'")
    assert validate_readonly('SELECT "set" FROM sales')


@pytest.mark.parametrize("query", [
    "PrAgMa version",
    "INSTALL httpfs",
    "LOAD httpfs",
    "EXPORT DATABASE 'x'",
    "IMPORT DATABASE 'x'",
    "CALL some_function()",
    "SELECT 1; /* hidden */ DELETE FROM sales",
    "WITH q AS (SELECT 1) UPDATE sales SET x = 2",
    "SELECT * FROM read_csv('/private/file.csv')",
    "SELECT * FROM parquet_scan('C:/private/file.parquet')",
])
def test_guard_rejects_adversarial_sql_variants(query):
    with pytest.raises(ValueError):
        validate_readonly(query)


@pytest.mark.parametrize("query", [
    "SELECT 'DROP; DELETE; COPY' AS text_value",
    'SELECT "update" AS "update" FROM (SELECT 1 AS "update") nested_query',
    "WITH q AS (SELECT 1 AS n) SELECT n FROM q UNION SELECT 2",
    "SELECT (SELECT 1) AS nested_value",
    "EXPLAIN SELECT 1",
    "DESCRIBE SELECT 1 AS safe_value",
])
def test_guard_preserves_legitimate_nested_read_queries(query):
    assert validate_readonly(query)


def test_engine_blocks_external_access(tmp_path):
    import duckdb
    from app.engine import DataEngine
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    eng = DataEngine()
    eng.load_csv("sales", "data/sales.csv")
    # bypass the SQL guard on purpose: the connection itself must refuse
    with pytest.raises(duckdb.Error):
        eng.con.execute(f"SELECT * FROM read_text('{secret}')").fetchall()
    with pytest.raises(duckdb.Error):
        eng.con.execute("SET enable_external_access = true")


@pytest.mark.parametrize("q", [
    "SELECT \"a'b\" FROM sales; DROP TABLE sales; --'",
    "SELECT 'x\"', 1; DROP TABLE sales; SELECT \"'",
])
def test_guard_quote_desync_does_not_hide_second_statement(q):
    with pytest.raises(ValueError):
        validate_readonly(q)
