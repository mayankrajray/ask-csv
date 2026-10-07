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
