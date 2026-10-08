"""Analytics module — Auto-Dashboard generation, Data Quality Scorecard,
Time-Series Forecasting, Executive Report Generation, and Observability.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any

import numpy as np
import pandas as pd

from . import charts
from .engine import DataEngine

NUMERIC_TYPES = ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "HUGEINT", "BIGINT", "TINYINT", "SMALLINT")

# Identifier column name regex patterns that shouldn't be treated as additive measures
ID_NAME_PATTERNS = re.compile(
    r"(^|_)("
    r"id|pincode|pin_code|postal|zip|zipcode|phone|mobile|contact|aadhaar|uid|pan|ssn|"
    r"roll|enrollment|enrolment|sr_no|sno|serial|account|card|lat|latitude|long|longitude|"
    r"year|month|day|date|rank|index"
    r")($|_)",
    re.IGNORECASE,
)


def classify_columns(df: pd.DataFrame, schema: list[dict]) -> dict[str, list[str]]:
    """Classify columns into measures (additive metrics), categories (dimensions), dates, and identifiers."""
    col_types = {c["name"]: c["type"].upper() for c in schema}

    date_cols: list[str] = []
    id_cols: list[str] = []
    measure_cols: list[str] = []
    cat_cols: list[str] = []

    for c in df.columns:
        c_str = str(c).strip()
        c_lower = c_str.lower()
        typ = col_types.get(c, "VARCHAR")
        is_num = any(h in typ for h in NUMERIC_TYPES) or pd.api.types.is_numeric_dtype(df[c])

        # 1. Date / time columns
        if any(w in c_lower for w in ("date", "time", "month", "year", "period", "timestamp", "dt")):
            date_cols.append(c_str)
            continue

        # 2. Identifier / Postal / Phone / Serial columns
        if (
            ID_NAME_PATTERNS.search(c_lower)
            or c_lower in ("sr no", "sr. no.", "sl no", "s.no", "no", "code", "pincode", "zip", "pin")
        ):
            id_cols.append(c_str)
            continue

        if is_num:
            non_null = df[c].dropna()
            # If values look like pincodes or constant high numbers
            if len(non_null) > 0:
                try:
                    min_v, max_v = float(non_null.min()), float(non_null.max())
                    # Pincodes / IDs check: 6-digit values with high uniqueness
                    if (
                        min_v >= 100000
                        and max_v <= 999999
                        and non_null.nunique() / max(1, len(non_null)) > 0.4
                        and any(p in c_lower for p in ("pin", "code", "postal", "zip"))
                    ):
                        id_cols.append(c_str)
                        continue
                except Exception:
                    pass
            measure_cols.append(c_str)
        else:
            cat_cols.append(c_str)

    return {
        "dates": date_cols,
        "measures": measure_cols,
        "ids": id_cols,
        "categories": cat_cols,
    }


# ------------------------------------------------------------------ 1. Auto Dashboard
def generate_dashboard(engine: DataEngine, table: str) -> dict:
    """Generate an executive dashboard with smart, business-meaningful KPIs and visual breakdown charts."""
    if table not in engine.tables:
        raise ValueError(f"Table '{table}' not found.")

    df = engine.execute(f'SELECT * FROM "{table}" LIMIT 5000')
    if df.empty:
        return {"ok": False, "error": "Table is empty"}

    schema = engine.schema(table)[0]["columns"]
    classes = classify_columns(df, schema)
    measures = classes["measures"]
    categories = classes["categories"]
    dates = classes["dates"]
    ids = classes["ids"]

    total_rows = len(df)
    kpis = [{"label": "Total Records", "value": f"{total_rows:,}", "sub": f"in table '{table}'"}]

    # 1. Compute quantitative KPIs for genuine measures
    for col in measures[:3]:
        s = df[col].sum()
        avg = df[col].mean()
        is_currency = any(w in col.lower() for w in ("rev", "price", "sale", "cost", "amt", "amount", "spend", "salary", "budget"))
        val_str = f"₹{s:,.0f}" if is_currency else f"{s:,.0f}"
        col_label = f"Total {col.replace('_', ' ').title()}"
        kpis.append({"label": col_label, "value": val_str, "sub": f"Avg: {avg:,.2f}"})

    # 2. If fewer than 4 KPIs, fill with unique counts for dimensions or identifiers (e.g. Unique States, Unique Districts, Unique Pincodes)
    dim_candidates = categories + ids
    for col in dim_candidates:
        if len(kpis) >= 4:
            break
        uniq_count = df[col].nunique(dropna=True)
        col_label = f"Unique {col.replace('_', ' ').title()}s"
        kpis.append({"label": col_label, "value": f"{uniq_count:,}", "sub": f"Distinct in dataset"})

    chart_specs = []
    target_metric = measures[0] if measures else None

    # Chart 1: Category Distribution / Breakdown
    if categories and target_metric:
        cat = categories[0]
        agg_df = df.groupby(cat, as_index=False)[target_metric].sum().sort_values(by=target_metric, ascending=False).head(8)
        spec1 = charts.build_spec(agg_df, "bar", cat, target_metric, f"{target_metric.replace('_', ' ').title()} by {cat.replace('_', ' ').title()}")
        chart_specs.append({"id": "chart-cat-bar", "title": f"{target_metric.replace('_', ' ').title()} Breakdown", "spec": spec1})
    elif categories:
        cat = categories[0]
        agg_df = df[cat].value_counts().head(8).reset_index()
        agg_df.columns = [cat, "record_count"]
        spec1 = charts.build_spec(agg_df, "bar", cat, "record_count", f"Records by {cat.replace('_', ' ').title()}")
        chart_specs.append({"id": "chart-cat-bar", "title": f"Records by {cat.replace('_', ' ').title()}", "spec": spec1})

    # Chart 2: Donut / Pie Share Chart
    if len(categories) > 1 and target_metric:
        cat2 = categories[1]
        agg_df2 = df.groupby(cat2, as_index=False)[target_metric].sum().sort_values(by=target_metric, ascending=False).head(6)
        spec2 = charts.build_spec(agg_df2, "donut", cat2, target_metric, f"{target_metric.replace('_', ' ').title()} Share by {cat2.replace('_', ' ').title()}")
        chart_specs.append({"id": "chart-share-donut", "title": f"Share by {cat2.replace('_', ' ').title()}", "spec": spec2})
    elif categories and target_metric:
        cat = categories[0]
        agg_df2 = df.groupby(cat, as_index=False)[target_metric].sum().sort_values(by=target_metric, ascending=False).head(6)
        spec2 = charts.build_spec(agg_df2, "donut", cat, target_metric, f"{target_metric.replace('_', ' ').title()} Share by {cat.replace('_', ' ').title()}")
        chart_specs.append({"id": "chart-share-donut", "title": f"Share by {cat.replace('_', ' ').title()}", "spec": spec2})
    elif categories:
        cat = categories[0]
        agg_df2 = df[cat].value_counts().head(6).reset_index()
        agg_df2.columns = [cat, "record_count"]
        spec2 = charts.build_spec(agg_df2, "donut", cat, "record_count", f"Record Share by {cat.replace('_', ' ').title()}")
        chart_specs.append({"id": "chart-share-donut", "title": f"Share by {cat.replace('_', ' ').title()}", "spec": spec2})

    # Chart 3: Time Trend Chart
    if dates:
        date_col = dates[0]
        if target_metric:
            agg_date = df.groupby(date_col, as_index=False)[target_metric].sum().sort_values(by=date_col).head(24)
            spec3 = charts.build_spec(agg_date, "line", date_col, target_metric, f"{target_metric.replace('_', ' ').title()} Over Time")
            chart_specs.append({"id": "chart-time-line", "title": f"{target_metric.replace('_', ' ').title()} Over Time", "spec": spec3})
        else:
            agg_date = df[date_col].value_counts().reset_index()
            agg_date.columns = [date_col, "record_count"]
            agg_date = agg_date.sort_values(by=date_col).head(24)
            spec3 = charts.build_spec(agg_date, "line", date_col, "record_count", f"Activity Volume Over Time")
            chart_specs.append({"id": "chart-time-line", "title": "Volume Over Time", "spec": spec3})
    elif len(measures) > 1 and categories:
        m2 = measures[1]
        cat = categories[0]
        agg_m2 = df.groupby(cat, as_index=False)[m2].sum().sort_values(by=m2, ascending=False).head(8)
        spec3 = charts.build_spec(agg_m2, "bar", cat, m2, f"{m2.replace('_', ' ').title()} by {cat.replace('_', ' ').title()}")
        chart_specs.append({"id": "chart-m2-bar", "title": f"{m2.replace('_', ' ').title()} by {cat.replace('_', ' ').title()}", "spec": spec3})

    # Chart 4: Top 5 Entities / Detailed breakdown
    if len(categories) > 1:
        # Prefer the category with higher distinct values (e.g. district, product, city)
        sub_cat = categories[1] if len(categories) > 1 else categories[0]
        if len(categories) > 2 and df[categories[2]].nunique() > df[categories[1]].nunique():
            sub_cat = categories[2]
        if target_metric:
            agg_top = df.groupby(sub_cat, as_index=False)[target_metric].sum().sort_values(by=target_metric, ascending=False).head(5)
            spec4 = charts.build_spec(agg_top, "bar", sub_cat, target_metric, f"Top 5 {sub_cat.replace('_', ' ').title()}")
            chart_specs.append({"id": "chart-top-entities", "title": f"Top 5 {sub_cat.replace('_', ' ').title()}", "spec": spec4})
        else:
            agg_top = df[sub_cat].value_counts().head(5).reset_index()
            agg_top.columns = [sub_cat, "record_count"]
            spec4 = charts.build_spec(agg_top, "bar", sub_cat, "record_count", f"Top 5 {sub_cat.replace('_', ' ').title()}")
            chart_specs.append({"id": "chart-top-entities", "title": f"Top 5 {sub_cat.replace('_', ' ').title()}", "spec": spec4})

    return {
        "ok": True,
        "table": table,
        "kpis": kpis,
        "charts": chart_specs,
        "total_rows": total_rows,
        "num_columns": len(schema),
    }


# ------------------------------------------------------------------ 2. Data Quality Scorecard
def audit_data_quality(engine: DataEngine, table: str) -> dict:
    """Run comprehensive data quality checks, scoring health out of 100."""
    df = engine.execute(f'SELECT * FROM "{table}" LIMIT 100000')
    total_rows = len(df)
    total_cols = len(df.columns)
    if total_rows == 0:
        return {"ok": False, "error": "Table is empty"}
    total_cells = total_rows * total_cols if total_rows and total_cols else 1

    null_count = int(df.isna().sum().sum())
    dup_count = int(df.duplicated().sum())
    completeness = round(max(0, 100 - (null_count / total_cells * 100)), 1)
    uniqueness = round(max(0, 100 - (dup_count / (total_rows or 1) * 100)), 1)

    # Health score formula
    health_score = int(round(completeness * 0.6 + uniqueness * 0.4))

    col_stats = []
    for c in df.columns:
        nulls = int(df[c].isna().sum())
        null_pct = round(nulls / (total_rows or 1) * 100, 1)
        distinct = int(df[c].nunique(dropna=True))
        typ = str(df[c].dtype)
        is_num = pd.api.types.is_numeric_dtype(df[c])

        status = "Healthy"
        issues = []
        if null_pct > 20:
            status = "Warning"
            issues.append(f"{null_pct}% nulls")
        if distinct == 1 and total_rows > 1:
            status = "Warning"
            issues.append("Constant column (zero variance)")
        if is_num and not df[c].dropna().empty:
            mean = float(df[c].mean())
            std = float(df[c].std() or 0)
            if std > 0:
                outliers = int(((df[c] - mean).abs() > 3 * std).sum())
                if outliers > 0:
                    issues.append(f"{outliers} outliers (>3σ)")

        col_stats.append({
            "name": c,
            "type": typ,
            "nulls": nulls,
            "null_pct": null_pct,
            "distinct": distinct,
            "status": status,
            "issues": issues or ["None"],
        })

    recommendations = []
    if null_count > 0:
        recommendations.append(f"Consider imputing or filtering {null_count:,} missing values across columns.")
    if dup_count > 0:
        recommendations.append(f"Found {dup_count:,} duplicate rows. Consider de-duplicating before training or modeling.")
    if not recommendations:
        recommendations.append("Dataset is clean with 100% completeness and no duplicate rows.")

    return {
        "ok": True,
        "table": table,
        "health_score": health_score,
        "completeness_pct": completeness,
        "uniqueness_pct": uniqueness,
        "total_rows": total_rows,
        "total_columns": total_cols,
        "null_cells": null_count,
        "duplicate_rows": dup_count,
        "columns": col_stats,
        "recommendations": recommendations,
    }


# ------------------------------------------------------------------ 3. Time-Series Forecasting
def forecast_metric(engine: DataEngine, table: str, date_col: str | None = None,
                    metric_col: str | None = None, periods: int = 6) -> dict:
    """Project a linear trend with a residual-spread band and intelligent metric selection."""
    if periods < 1:
        return {"ok": False, "error": "Forecast periods must be a positive integer."}
    df = engine.execute(f'SELECT * FROM "{table}" LIMIT 50000')
    if df.empty:
        return {"ok": False, "error": "Table is empty"}

    schema = engine.schema(table)[0]["columns"]
    classes = classify_columns(df, schema)
    dates = classes["dates"]
    measures = classes["measures"]
    categories = classes["categories"]
    ids = classes["ids"]

    if date_col is not None and date_col not in df.columns:
        return {"ok": False, "error": f"Date column '{date_col}' was not found in table '{table}'."}
    if metric_col is not None and metric_col != "__record_count__" and metric_col not in df.columns:
        return {"ok": False, "error": f"Metric column '{metric_col}' was not found in table '{table}'."}

    # Available date columns
    available_dates = dates if dates else (categories if categories else list(df.columns))
    date_c = date_col if (date_col and date_col in df.columns) else (available_dates[0] if available_dates else None)

    # Available metrics
    available_metrics = measures.copy()
    if not available_metrics:
        available_metrics = ["__record_count__"]
    else:
        available_metrics.append("__record_count__")

    if metric_col and metric_col != "__record_count__" and metric_col in df.columns and \
            not pd.api.types.is_numeric_dtype(df[metric_col]):
        return {"ok": False, "error": f"Metric column '{metric_col}' must be numeric."}
    if metric_col and (metric_col in df.columns or metric_col == "__record_count__"):
        metric_c = metric_col
    else:
        metric_c = measures[0] if measures else "__record_count__"

    if not date_c:
        return {"ok": False, "error": f"No date or time column identified in table '{table}'."}

    # Prepare time-series aggregation
    if metric_c == "__record_count__":
        agg = df[date_c].value_counts().reset_index()
        agg.columns = [date_c, "record_count"]
        metric_name = "Record Volume (Count)"
        metric_plot_col = "record_count"
        agg = agg.sort_values(by=date_c)
    else:
        agg = df.groupby(date_c, as_index=False)[metric_c].sum().sort_values(by=date_c)
        metric_name = metric_c.replace("_", " ").title()
        metric_plot_col = metric_c

    n = len(agg)
    if n < 3:
        return {"ok": False, "error": f"Not enough time points in '{date_c}' (found {n}, need at least 3)."}

    x = np.arange(n)
    y = agg[metric_plot_col].to_numpy(dtype=float)

    # Linear trend fit
    slope, intercept = np.polyfit(x, y, 1)
    residuals = y - (slope * x + intercept)
    std_err = float(np.std(residuals)) if len(residuals) else 1.0

    # Projected periods
    future_x = np.arange(n, n + periods)
    future_dates = [f"Period +{i+1}" for i in range(periods)]

    forecast_values = slope * future_x + intercept
    upper_bounds = forecast_values + 1.96 * std_err
    
    # If historical data is all non-negative, clamp lower bounds and forecast values at 0
    if (y >= 0).all():
        forecast_values = np.maximum(0, forecast_values)
        lower_bounds = np.maximum(0, forecast_values - 1.96 * std_err)
        upper_bounds = np.maximum(0, upper_bounds)
    else:
        lower_bounds = forecast_values - 1.96 * std_err

    hist_dates = [str(d) for d in agg[date_c]]
    hist_values = [round(float(v), 2) for v in y]

    # Plotly Forecast Chart Spec
    spec = {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": "Historical Actual",
                "x": hist_dates,
                "y": hist_values,
                "line": {"color": "#412ce7", "width": 2.5},
            },
            {
                "type": "scatter",
                "mode": "lines+markers",
                "name": f"Forecast (+{periods} periods)",
                "x": future_dates,
                "y": [round(float(v), 2) for v in forecast_values],
                "line": {"color": "#ff6b4a", "width": 2.5, "dash": "dash"},
            },
            {
                "type": "scatter",
                "mode": "lines",
                "name": "Upper 1.96× Residual SD",
                "x": future_dates,
                "y": [round(float(v), 2) for v in upper_bounds],
                "line": {"color": "rgba(217, 119, 87, 0.3)", "width": 1},
                "showlegend": False,
            },
            {
                "type": "scatter",
                "mode": "lines",
                "fill": "tonexty",
                "name": "Residual Spread Band",
                "x": future_dates,
                "y": [round(float(v), 2) for v in lower_bounds],
                "fillcolor": "rgba(217, 119, 87, 0.12)",
                "line": {"color": "rgba(217, 119, 87, 0.3)", "width": 1},
            },
        ],
        "layout": {
            "title": f"Forecasting {metric_name} ({periods} Periods Ahead)",
            "xaxis": {"title": date_c.replace('_', ' ').title()},
            "yaxis": {"title": metric_name},
            "paper_bgcolor": "#eff4ff",
            "plot_bgcolor": "#eff4ff",
            "margin": {"l": 60, "r": 20, "t": 40, "b": 40},
        },
    }

    mean_y = float(y.mean()) if y.mean() != 0 else 1.0
    return {
        "ok": True,
        "table": table,
        "date_column": date_c,
        "metric_column": metric_name,
        "raw_metric": metric_c,
        "available_dates": available_dates,
        "available_metrics": available_metrics,
        "historical_points": n,
        "forecast_periods": periods,
        "trend_direction": "Flat" if np.isclose(slope, 0.0, rtol=1e-9, atol=1e-12)
        else "Upward" if slope > 0 else "Downward",
        "growth_rate_pct": round((slope / mean_y) * 100, 2),
        "spec": spec,
        "projections": [
            {
                "period": future_dates[i],
                "forecast": round(float(forecast_values[i]), 2),
                "lower_bound": round(float(lower_bounds[i]), 2),
                "upper_bound": round(float(upper_bounds[i]), 2),
            }
            for i in range(periods)
        ],
    }


# ------------------------------------------------------------------ 4. Executive Report
def generate_report(engine: DataEngine, table: str) -> dict:
    """Generate a printable Executive Summary Report in Markdown & HTML formats."""
    dash = generate_dashboard(engine, table)
    if not dash.get("ok"):
        return dash
    qual = audit_data_quality(engine, table)
    if not qual.get("ok"):
        return qual

    kpi_lines = "\n".join(f"- **{k['label']}**: `{k['value']}` ({k.get('sub', '')})" for k in dash.get("kpis", []))
    recs = "\n".join(f"1. {r}" for r in qual.get("recommendations", []))

    md_report = f"""# Executive Data Analysis Report
**Dataset:** `{table}`  
**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}  
**Data Health Score:** {qual['health_score']}/100 ({qual['completeness_pct']}% completeness)

---

## 1. Key Performance Indicators (KPIs)
{kpi_lines}

---

## 2. Data Health & Quality Summary
- **Total Rows:** {qual['total_rows']:,}
- **Total Columns:** {qual['total_columns']}
- **Missing / Null Cells:** {qual['null_cells']:,}
- **Duplicate Records:** {qual['duplicate_rows']:,}

### Quality Recommendations:
{recs}

---

## 3. Schema Digest
| Column Name | Type | Null Count | Null % | Status |
| :--- | :--- | :--- | :--- | :--- |
"""
    for c in qual.get("columns", []):
        md_report += f"| {c['name']} | `{c['type']}` | {c['nulls']} | {c['null_pct']}% | {c['status']} |\n"

    return {
        "ok": True,
        "table": table,
        "markdown": md_report,
        "health_score": qual["health_score"],
        "kpis": dash.get("kpis", []),
    }
