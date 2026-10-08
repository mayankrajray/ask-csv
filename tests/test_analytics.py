from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config
from app import analytics, charts
from app.anomalies import analyse_dataframe
from app.engine import DataEngine
from app.main import app, sessions, _new_session
from app.tools import ToolBox


SALES_CSV = (
    "region,product,sales,order_date\n"
    "North,A,100,2025-01-01\n"
    "North,B,200,2025-01-02\n"
    "South,A,150,2025-01-03\n"
    "South,B,50,2025-01-04\n"
    "West,A,500,2025-01-05\n"
)


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "sales.csv"
    path.write_text(SALES_CSV, encoding="utf-8")
    result = DataEngine()
    result.load_csv("sales", str(path))
    return result


@pytest.fixture
def analytics_client(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_dir)
    client = TestClient(app)
    session_ids = []
    yield client, session_ids
    for sid in session_ids:
        sessions.pop(sid, None)


def _upload(client, session_ids, name, content):
    response = client.post("/api/upload", files=[("files", (name, content.encode(), "text/csv"))])
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    session_ids.append(sid)
    return sid


def test_sql_analysis_filter_group_order_limit_empty_and_errors(engine, tmp_path):
    tb = ToolBox(engine, "analytics-query-test")
    filtered = tb.run_sql("SELECT region, SUM(sales) AS total FROM sales "
                          "WHERE product = 'A' GROUP BY region ORDER BY total DESC LIMIT 2")
    assert filtered["ok"] is True
    assert filtered["rows"] == [["West", 500], ["South", 150]]
    empty = tb.run_sql("SELECT * FROM sales WHERE sales < 0")
    assert empty["ok"] is True and empty["row_count"] == 0 and empty["rows"] == []
    invalid = tb.run_sql("SELECT missing_column FROM sales")
    assert invalid["ok"] is False
    assert "Traceback" not in invalid["error"]
    protected = tb.run_sql("DELETE FROM sales")
    assert protected["ok"] is False
    assert "Only read-only" in protected["error"] or "Forbidden keyword" in protected["error"]


def test_dashboard_api_kpis_categories_and_empty_dataset(analytics_client):
    client, session_ids = analytics_client
    sid = _upload(client, session_ids, "sales.csv", SALES_CSV)
    response = client.get(f"/api/dashboard/{sid}/sales")
    assert response.status_code == 200
    dashboard = response.json()
    assert dashboard["ok"] is True
    assert dashboard["total_rows"] == 5
    assert dashboard["num_columns"] == 4
    assert {k["label"]: k["value"] for k in dashboard["kpis"]}["Total Sales"] == "₹1,000"
    assert any(chart["id"] == "chart-cat-bar" for chart in dashboard["charts"])

    missing_sid = _upload(client, session_ids, "missing.csv", "region,sales\nNorth,100\nSouth,\n")
    missing = client.get(f"/api/dashboard/{missing_sid}/missing").json()
    assert missing["ok"] is True
    assert missing["total_rows"] == 2
    assert {k["label"]: k["value"] for k in missing["kpis"]}["Total Sales"] == "₹100"

    empty_sid = _upload(client, session_ids, "empty.csv", "region,sales\n")
    empty = client.get(f"/api/dashboard/{empty_sid}/empty")
    assert empty.status_code == 200
    assert empty.json() == {"ok": False, "error": "Table is empty"}


def test_quality_api_reports_missing_duplicates_types_unique_empty_and_constants(analytics_client):
    client, session_ids = analytics_client
    raw = ("region,amount,tag,constant,empty,mixed\n"
           "North,100,a,x,,1\nNorth,100,a,x,,1\nSouth,,b,x,,not-numeric\nWest,500,,x,,3\n")
    sid = _upload(client, session_ids, "quality.csv", raw)
    response = client.get(f"/api/quality/{sid}/quality")
    assert response.status_code == 200
    quality = response.json()
    assert quality["total_rows"] == 4
    assert quality["total_columns"] == 6
    assert quality["null_cells"] == 6
    assert quality["duplicate_rows"] == 1
    assert quality["completeness_pct"] == 75.0
    assert quality["uniqueness_pct"] == 75.0
    cols = {c["name"]: c for c in quality["columns"]}
    assert cols["region"]["distinct"] == 3
    assert cols["amount"]["type"] in ("float64", "int64")
    assert cols["empty"]["distinct"] == 0
    assert cols["mixed"]["type"] in ("object", "str", "string")
    assert cols["mixed"]["distinct"] == 3
    assert "Constant column" in " ".join(cols["constant"]["issues"])

    empty_sid = _upload(client, session_ids, "blank.csv", "a,b\n")
    empty = client.get(f"/api/quality/{empty_sid}/blank")
    assert empty.status_code == 200
    assert empty.json() == {"ok": False, "error": "Table is empty"}


def test_anomaly_statistics_outlier_normal_small_constant_missing_and_non_numeric():
    frame = pd.DataFrame({"value": [1, 2, 2, 3, 3, 4, 4, 5, 6, 1000, None],
                          "constant": [7] * 11,
                          "label": ["x"] * 11})
    first = analyse_dataframe(frame, "iqr")
    second = analyse_dataframe(frame, "iqr")
    assert first == second
    result = next(c for c in first["columns"] if c["column"] == "value")
    assert result["count"] == 1
    assert result["upper_bound"] < 1000
    assert result["sample"][0]["value"] == "1000.0"
    zscore = next(c for c in analyse_dataframe(frame, "zscore")["columns"]
                  if c["column"] == "value")
    assert zscore["method"] == "zscore"
    assert zscore["count"] == 0  # the extreme value inflates standard deviation past the fixed 3σ threshold
    assert all(c["column"] != "constant" for c in first["columns"])
    assert all(c["column"] != "label" for c in first["columns"])
    assert analyse_dataframe(pd.DataFrame({"value": range(7)}))["columns"] == []
    assert analyse_dataframe(pd.DataFrame({"value": [4] * 10}))["columns"] == []
    assert analyse_dataframe(pd.DataFrame({"name": ["a", "b"]}))["columns"] == []


def test_anomaly_toolbox_normal_dataset_and_empty_table(engine, tmp_path):
    tb = ToolBox(engine, "anomaly-test")
    normal = tb.detect_anomalies("sales")
    assert normal["ok"] is True
    assert normal["columns"] == []

    empty_path = tmp_path / "empty.csv"
    empty_path.write_text("value\n", encoding="utf-8")
    empty_engine = DataEngine()
    empty_engine.load_csv("empty", str(empty_path))
    result = ToolBox(empty_engine, "anomaly-empty-test").detect_anomalies("empty")
    assert result["ok"] is True
    assert result["columns"] == []


def test_forecast_api_sufficient_missing_constant_and_insufficient_series(analytics_client):
    client, session_ids = analytics_client
    series = ("month,sales\n2025-01-01,10\n2025-02-01,\n2025-04-01,30\n"
              "2025-05-01,40\n2025-06-01,50\n")
    sid = _upload(client, session_ids, "series.csv", series)
    response = client.get(f"/api/forecast/{sid}/series?date_col=month&metric_col=sales&periods=3")
    assert response.status_code == 200
    forecast = response.json()
    assert forecast["ok"] is True
    assert forecast["historical_points"] == 5
    assert forecast["forecast_periods"] == 3
    assert len(forecast["projections"]) == 3
    assert all(np.isfinite(p["forecast"]) for p in forecast["projections"])

    short_sid = _upload(client, session_ids, "short.csv", "month,sales\n2025-01,10\n2025-02,20\n")
    short = client.get(f"/api/forecast/{short_sid}/short?date_col=month&metric_col=sales")
    assert short.status_code == 200
    assert short.json()["ok"] is False
    assert "at least 3" in short.json()["error"]

    constant_sid = _upload(client, session_ids, "constant.csv",
                           "month,sales\n2025-01,5\n2025-02,5\n2025-03,5\n")
    constant = client.get(f"/api/forecast/{constant_sid}/constant?date_col=month&metric_col=sales&periods=2")
    assert constant.json()["ok"] is True
    assert [p["forecast"] for p in constant.json()["projections"]] == [5.0, 5.0]


def test_forecast_invalid_periods_and_non_numeric_metric_fail_safely(analytics_client):
    client, session_ids = analytics_client
    sid = _upload(client, session_ids, "sales.csv", SALES_CSV)
    invalid_type = client.get(f"/api/forecast/{sid}/sales?periods=abc")
    assert invalid_type.status_code == 422
    assert "Traceback" not in invalid_type.text
    zero = client.get(f"/api/forecast/{sid}/sales?periods=0&date_col=order_date&metric_col=sales")
    assert zero.status_code == 200
    assert zero.json()["ok"] is False

    # An explicitly selected categorical metric is rejected as a controlled failure.
    non_numeric = client.get(f"/api/forecast/{sid}/sales?date_col=order_date&metric_col=region&periods=1")
    assert non_numeric.status_code == 200
    assert non_numeric.json()["ok"] is False
    assert "must be numeric" in non_numeric.json()["error"]


def test_chart_types_and_invalid_or_empty_chart_queries(engine):
    frame = pd.DataFrame({"region": ["North", "South"], "sales": [100, 150]})
    cases = {
        "line": ("scatter", "lines"),
        "bar": ("bar", None),
        "scatter": ("scatter", "markers"),
        "histogram": ("histogram", None),
        "pie": ("pie", None),
        "donut": ("pie", None),
    }
    for kind, (expected_type, expected_mode) in cases.items():
        spec = charts.build_spec(frame, kind, "sales" if kind == "histogram" else "region",
                                 "sales", f"{kind} example")
        trace = spec["data"][0]
        assert trace["type"] == expected_type
        if expected_mode:
            assert trace["mode"] == expected_mode
        if kind in ("bar", "line", "scatter"):
            assert trace["x"] == ["North", "South"]
            assert trace["y"] == [100.0, 150.0]
        if kind in ("pie", "donut"):
            assert trace["labels"] == ["North", "South"]
            assert trace["values"] == [100.0, 150.0]
    assert charts.build_spec(frame, "not-a-chart", "region", "sales", "fallback")["data"][0]["type"] == "bar"

    tb = ToolBox(engine, "chart-test")
    empty = tb.build_chart("SELECT region, sales FROM sales WHERE 1=0", "line", "region", "sales")
    assert empty["ok"] is False and "no rows" in empty["error"]
    bad_sql = tb.build_chart("SELECT missing FROM sales", "bar", "region", "sales")
    assert bad_sql["ok"] is False
    invalid_params = tb.build_chart("SELECT region FROM sales", "bar", "region", "missing")
    assert invalid_params["ok"] is False
    non_numeric = tb.build_chart("SELECT region FROM sales", "bar", "region", "region")
    assert non_numeric["ok"] is False


def test_report_api_and_empty_report_behavior(analytics_client):
    client, session_ids = analytics_client
    sid = _upload(client, session_ids, "sales.csv", SALES_CSV)
    response = client.get(f"/api/report/{sid}/sales")
    assert response.status_code == 200
    report = response.json()
    assert report["ok"] is True
    assert report["health_score"] == 100
    assert "Executive Data Analysis Report" in report["markdown"]
    assert len(report["kpis"]) >= 2

    empty_sid = _upload(client, session_ids, "empty.csv", "region,sales\n")
    empty = client.get(f"/api/report/{empty_sid}/empty")
    assert empty.status_code == 200
    assert empty.json() == {"ok": False, "error": "Table is empty"}

    multi_metric_sid = _upload(client, session_ids, "metrics.csv",
                               "region,sales,cost\nNorth,100,40\nSouth,200,80\n")
    multi_metric = client.get(f"/api/report/{multi_metric_sid}/metrics").json()
    assert {kpi["label"] for kpi in multi_metric["kpis"]} >= {"Total Sales", "Total Cost"}


def test_sql_result_export_creates_valid_bounded_csv_and_empty_results_do_not_export(
        engine, tmp_path, monkeypatch):
    export_root = tmp_path / "exports"
    monkeypatch.setattr(config, "EXPORT_DIR", export_root)
    monkeypatch.setattr(config, "MAX_ROWS_TO_LLM", 2)
    tb = ToolBox(engine, "safe-session-id")
    result = tb.run_sql("SELECT region, sales FROM sales ORDER BY sales")
    assert result["ok"] is True
    assert result["row_count"] == 5
    assert len(result["rows"]) == 2
    url = result["export"]
    filename = url.rsplit("/", 1)[1]
    assert Path(filename).name == filename
    assert filename.endswith(".csv")
    exported = tb.export_dir / filename
    assert exported.is_file()
    assert pd.read_csv(exported).shape == (5, 2)
    assert list(pd.read_csv(exported).columns) == ["region", "sales"]

    empty = tb.run_sql("SELECT region, sales FROM sales WHERE 1=0")
    assert empty["ok"] is True and empty["row_count"] == 0
    assert "export" not in empty


def test_api_errors_for_missing_session_table_dataset_and_malformed_forecast(analytics_client):
    client, session_ids = analytics_client
    assert client.get("/api/dashboard/no-such-session/sales").status_code == 404
    sid = _upload(client, session_ids, "sales.csv", SALES_CSV)
    assert client.get(f"/api/dashboard/{sid}/missing").status_code == 404
    assert client.get(f"/api/forecast/{sid}/sales?periods=bad").status_code == 422

    empty_sess = client.post("/api/upload", files=[("files", ("blank.csv", b"", "text/csv"))])
    assert empty_sess.status_code == 400
    assert "Traceback" not in empty_sess.text

    no_data = client.post("/api/chat", json={"session_id": "missing", "message": "analyze"})
    assert no_data.status_code == 404
    empty_session = _new_session(provider="demo")
    session_ids.append(empty_session["id"])
    no_dataset = client.post("/api/chat", json={"session_id": empty_session["id"], "message": "analyze"})
    assert no_dataset.status_code == 400
    assert "Traceback" not in no_dataset.text


def test_sql_numeric_boundaries_and_joined_aggregation_preserve_rows(engine, tmp_path):
    customers_path = tmp_path / "customers.csv"
    sales_path = tmp_path / "joined_sales.csv"
    customers_path.write_text("customer_id,region\n1,North\n1,North\n2,South\n3,West\n", encoding="utf-8")
    sales_path.write_text("customer_id,amount\n1,0.10\n1,-0.10\n2,0\n9,50\n", encoding="utf-8")
    engine.load_csv("customers", str(customers_path))
    engine.load_csv("joined_sales", str(sales_path))
    tb = ToolBox(engine, "analytics-join-correctness")

    result = tb.run_sql("SELECT c.region, SUM(s.amount) AS total, AVG(s.amount) AS mean, "
                        "MIN(s.amount) AS low, MAX(s.amount) AS high, COUNT(*) AS n "
                        "FROM customers c JOIN joined_sales s USING (customer_id) "
                        "WHERE s.amount >= -0.1 GROUP BY c.region ORDER BY c.region")
    assert result["ok"] is True
    actual = {row[0]: row[1:] for row in result["rows"]}
    assert actual["North"][0] == pytest.approx(0.0)
    assert actual["North"][1] == pytest.approx(0.0)
    assert actual["North"][2] == pytest.approx(-0.1)
    assert actual["North"][3] == pytest.approx(0.1)
    assert actual["North"][4] == 4  # duplicate dimension keys multiply matching fact rows
    assert actual["South"] == [0.0, 0.0, 0.0, 0.0, 1]
    assert "West" not in actual and "customer_id=9" not in str(actual)


def test_forecast_flat_and_decreasing_trends_have_correct_direction(engine, tmp_path):
    flat_path = tmp_path / "flat.csv"
    down_path = tmp_path / "down.csv"
    flat_path.write_text("date,value\n2025-01-01,5\n2025-02-01,5\n2025-03-01,5\n", encoding="utf-8")
    down_path.write_text("date,value\n2025-01-01,3\n2025-02-01,2\n2025-03-01,1\n", encoding="utf-8")
    engine.load_csv("flat", str(flat_path))
    engine.load_csv("down", str(down_path))

    flat = analytics.forecast_metric(engine, "flat", "date", "value", periods=2)
    decreasing = analytics.forecast_metric(engine, "down", "date", "value", periods=1)
    assert flat["ok"] is True
    assert [point["forecast"] for point in flat["projections"]] == [5.0, 5.0]
    assert flat["trend_direction"] == "Flat"
    assert flat["growth_rate_pct"] == 0
    assert flat["spec"]["data"][2]["name"] == "Upper 1.96× Residual SD"
    assert flat["spec"]["data"][3]["name"] == "Residual Spread Band"
    assert all("Confidence" not in trace.get("name", "") for trace in flat["spec"]["data"])
    assert decreasing["trend_direction"] == "Downward"
    assert decreasing["projections"][0]["forecast"] == pytest.approx(0.0)


def test_forecast_rejects_explicit_unknown_columns_instead_of_using_another_metric(engine):
    invalid_date = analytics.forecast_metric(engine, "sales", date_col="not_a_date", metric_col="sales")
    invalid_metric = analytics.forecast_metric(engine, "sales", date_col="order_date", metric_col="not_a_metric")
    assert invalid_date["ok"] is False and "date column" in invalid_date["error"].lower()
    assert invalid_metric["ok"] is False and "metric column" in invalid_metric["error"].lower()


def test_chart_spec_preserves_missing_axis_values_instead_of_turning_them_into_zero():
    frame = pd.DataFrame({"category": ["known", None, "blank"], "value": [2.5, 8.0, None]})
    spec = charts.build_spec(frame, "bar", "category", "value", "missing values")
    trace = spec["data"][0]
    assert trace["x"] == ["known", None, "blank"]
    assert trace["y"] == [2.5, 8.0, None]
