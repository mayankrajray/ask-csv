"""Deterministic offline analyst quality gate; no provider APIs are called."""
from pathlib import Path

import numpy as np
import pytest

from app import analytics, config
from app.demo_agent import DemoAgent
from app.engine import DataEngine
from app.tools import ToolBox

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def evaluation_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exports")
    engine = DataEngine()
    yield engine, ToolBox(engine, "evaluation-session")
    engine.close()


def _load(engine, table, fixture):
    engine.load_csv(table, str(FIXTURES / fixture))


def test_aggregation_total_sales(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "sales", "sales.csv")

    result = tools.run_sql("SELECT SUM(units * price) AS total_sales FROM sales")

    assert result["ok"] is True
    assert result["rows"] == [[6600]]


def test_grouping_finds_highest_sales_region(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "sales", "sales.csv")

    result = tools.run_sql(
        "SELECT region, SUM(units * price) AS sales FROM sales "
        "GROUP BY region ORDER BY sales DESC, region ASC"
    )

    assert result["ok"] is True
    assert result["rows"][0] == ["South", 2600]


def test_filtering_product_a_units(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "sales", "sales.csv")

    result = tools.run_sql("SELECT SUM(units) AS units FROM sales WHERE product = 'A'")

    assert result["ok"] is True
    assert result["rows"] == [[38]]


def test_top_two_regions_are_sorted_deterministically(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "sales", "sales.csv")

    result = tools.run_sql(
        "SELECT region, SUM(units * price) AS sales FROM sales "
        "GROUP BY region ORDER BY sales DESC, region ASC LIMIT 2"
    )

    assert result["ok"] is True
    assert result["rows"] == [["South", 2600], ["North", 2000]]


def test_multifile_join_aggregation_excludes_unmatched_customer(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "customers", "customers.csv")
    _load(engine, "transactions", "transactions.csv")

    result = tools.run_sql(
        "SELECT c.region, SUM(t.amount) AS total FROM customers c "
        "JOIN transactions t USING (customer_id) GROUP BY c.region ORDER BY c.region"
    )

    assert result["ok"] is True
    assert result["rows"] == [["North", 150], ["South", 70]]


def test_demo_agent_resolves_followup_from_prior_result(evaluation_env):
    engine, _ = evaluation_env
    _load(engine, "sales", "conversation.csv")
    agent = DemoAgent(engine, "evaluation-conversation")
    history = []

    first_events = list(agent.chat(history, "Which region has the highest sales?"))
    second_events = list(agent.chat(history, "How much did it sell?"))
    second_sql = next(event["sql"] for event in second_events if event.get("type") == "sql")
    second_text = "".join(event.get("text", "") for event in second_events)

    assert any("North" in event.get("text", "") for event in first_events)
    assert '"region" = \'North\'' in second_sql
    assert "North" in second_text and "4.0 lakh" in second_text


def test_iqr_anomaly_detection_finds_known_outlier(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "outliers", "anomalies.csv")

    result = tools.detect_anomalies("outliers", "value", "iqr")

    assert result["ok"] is True
    flagged = next(column for column in result["columns"] if column["column"] == "value")
    assert flagged["count"] == 1
    assert float(flagged["sample"][0]["value"]) == 1000


def test_linear_forecast_direction_points_and_finite_values(evaluation_env):
    engine, _ = evaluation_env
    _load(engine, "forecast", "forecast.csv")

    result = analytics.forecast_metric(engine, "forecast", "date", "value", periods=2)

    assert result["ok"] is True
    assert result["trend_direction"] == "Upward"
    assert len(result["projections"]) == 2
    assert all(np.isfinite(point["forecast"]) for point in result["projections"])


def test_quality_reports_known_missing_and_duplicate_rows(evaluation_env):
    engine, _ = evaluation_env
    _load(engine, "quality", "quality.csv")

    result = analytics.audit_data_quality(engine, "quality")

    assert result["ok"] is True
    assert result["total_rows"] == 3
    assert result["null_cells"] == 1
    assert result["duplicate_rows"] == 1


def test_chart_case_uses_requested_columns_and_correct_grouped_values(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "sales", "sales.csv")

    result = tools.build_chart(
        "SELECT region, SUM(units * price) AS sales FROM sales "
        "GROUP BY region ORDER BY sales DESC, region ASC",
        "bar", "region", "sales", "Sales by region",
    )

    assert result["ok"] is True
    trace = result["spec"]["data"][0]
    assert trace["type"] == "bar"
    assert trace["x"] == ["South", "North", "West"]
    assert trace["y"] == [2600.0, 2000.0, 2000.0]


def test_invalid_sql_returns_controlled_error_and_toolbox_remains_usable(evaluation_env):
    engine, tools = evaluation_env
    _load(engine, "sales", "sales.csv")

    error_text, events = tools.dispatch("run_sql", {"query": "DROP TABLE sales"})
    valid = tools.run_sql("SELECT COUNT(*) AS rows FROM sales")

    assert events[0]["type"] == "sql_error"
    assert "traceback" not in error_text.lower()
    assert valid["ok"] is True and valid["rows"] == [[5]]


def test_unknown_demo_question_returns_help_instead_of_crashing(evaluation_env):
    engine, _ = evaluation_env
    _load(engine, "sales", "sales.csv")
    agent = DemoAgent(engine, "evaluation-unknown-question")

    events = list(agent.chat([], "Please calculate a highly unusual unsupported thing"))
    text = "".join(event.get("text", "") for event in events)

    assert not any(event.get("type") == "error" for event in events)
    assert "offline demo mode" in text.lower()
    assert "try one of" in text.lower()
