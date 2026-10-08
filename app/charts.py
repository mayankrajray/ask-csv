"""Builds Plotly chart specifications (pure JSON) from a DataFrame.

The agent supplies an aggregated SQL query + axis mapping; this module turns
the small result set into a Plotly spec the frontend renders with Plotly.js.
No AI "draws" anything — geometry stays deterministic.
"""
from __future__ import annotations

import pandas as pd

PALETTE = ["#5b4dff", "#412ce7", "#c4c0ff", "#ff6b4a", "#565e74", "#bec6e0", "#c03e21"]

VALID_TYPES = ("bar", "line", "area", "scatter", "pie", "donut", "histogram")


def _numeric_values(series: pd.Series) -> list[float | None]:
    """Keep missing values missing in chart data rather than drawing them as zero."""
    values = pd.to_numeric(series, errors="coerce")
    return [None if pd.isna(value) else float(value) for value in values]


def _sorted_by_x(df: pd.DataFrame, x: str) -> pd.DataFrame:
    """Sort rows by x if x looks like a date/time (line charts read better)."""
    try:
        dts = pd.to_datetime(df[x], errors="coerce", format="mixed")
    except Exception:
        try:
            dts = pd.to_datetime(df[x], errors="coerce")
        except Exception:
            return df
    if dts.notna().mean() > 0.8:
        return df.iloc[dts.argsort().to_numpy()]
    return df


def build_spec(df: pd.DataFrame, chart_type: str, x: str | None, y: str | None, title: str | None) -> dict:
    ct = (chart_type or "bar").lower().strip()
    if ct not in VALID_TYPES:
        ct = "bar"

    if not df.empty:
        df = _sorted_by_x(df, x) if x in df.columns else df

    if x and x in df.columns:
        xs = df[x]
    else:
        xs = df.iloc[:, 0]
    xvals = [None if pd.isna(v) else str(v) for v in xs.tolist()]

    if ct in ("pie", "donut"):
        if y and y in df.columns:
            ys = df[y]
        else:
            ys = df.select_dtypes(include="number").iloc[:, 0]
        trace = {
            "type": "pie",
            "labels": xvals,
            "values": _numeric_values(ys),
            "hole": 0.55 if ct == "donut" else 0,
            "marker": {"colors": PALETTE},
            "textinfo": "label+percent",
        }
    else:
        if y and y in df.columns:
            ys = df[y]
        else:
            ys = df.select_dtypes(include="number").iloc[:, 0]
        trace = {
            "x": xvals,
            "y": _numeric_values(ys),
            "marker": {"color": PALETTE[0], "size": 7},
            "line": {"color": PALETTE[0], "width": 2.5},
        }
        if ct == "line":
            trace.update(type="scatter", mode="lines")
        elif ct == "area":
            trace.update(type="scatter", mode="lines", fill="tozeroy")
        elif ct == "scatter":
            trace.update(type="scatter", mode="markers")
        elif ct == "histogram":
            trace.update(type="histogram", xbins={}, marker={"color": PALETTE[0]})
            trace.pop("x", None)
            trace.pop("y", None)
            trace["x"] = xvals if x in (df.columns.tolist() or []) else None
            if not trace.get("x"):
                trace = {"type": "histogram", "x": xvals, "marker": {"color": PALETTE[0]}}
        else:  # bar
            trace.update(type="bar")

    layout = {
        "template": "plotly_white",
        "font": {"family": "Inter, system-ui, sans-serif", "size": 13, "color": "#0b1c30"},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "margin": {"l": 64, "r": 24, "t": 44, "b": 52},
        "xaxis": {"gridcolor": "#dce9ff"},
        "yaxis": {"gridcolor": "#dce9ff"},
        "showlegend": False,
        "hoverlabel": {"bgcolor": "#213145"},
    }
    if title:
        layout["title"] = {"text": title, "font": {"size": 15}}
    return {"data": [trace], "layout": layout}
