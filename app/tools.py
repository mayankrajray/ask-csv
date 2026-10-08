"""Agent tool layer — the single chokepoint between the LLM and the engine.

Every tool run by the agent (Gemini or demo mode) goes through ToolBox:
* SQL is validated read-only before execution.
* Results returned to the LLM are truncated to MAX_ROWS_TO_LLM rows;
  larger results are exported as a downloadable CSV instead.
* Charts are built from aggregated query results as pure Plotly JSON.
"""
from __future__ import annotations

import hashlib
import html
import time

import pandas as pd

import re

from . import charts, config
from .anomalies import analyse_dataframe
from .engine import DataEngine


def _fmt(v) -> str:
    if v is None:
        return ""
    return html.escape(str(v), quote=False)


def _untrusted_text(value, limit: int | None = None) -> str:
    """Escape markup delimiters before including dataset metadata in LLM text."""
    text = str(value)
    if limit is not None:
        text = text[:limit]
    return html.escape(re.sub(r"[\r\n\t]+", " ", text).strip(), quote=False)


def _sanitize_duckdb_sql(query: str) -> str:
    """Auto-fix common LLM SQL syntax mistakes for DuckDB (e.g. reversed strftime or uncast dates)."""
    q = query
    # 1. Fix reversed strftime('%Y-%m', col) -> strftime(TRY_CAST(col AS DATE), '%Y-%m')
    p1 = re.compile(r"strftime\s*\(\s*(['\"][^'\"]*?%[^'\"]*?['\"])\s*,\s*([a-zA-Z0-9_\.\"]+)\s*\)", re.IGNORECASE)
    q = p1.sub(r"strftime(TRY_CAST(\2 AS DATE), \1)", q)

    # 2. Fix uncast strftime(col, '%Y-%m') -> strftime(TRY_CAST(col AS DATE), '%Y-%m')
    p2 = re.compile(r"strftime\s*\(\s*(?!(?:TRY_CAST|CAST)\()([a-zA-Z0-9_\.\"]+)\s*,\s*(['\"][^'\"]+['\"])\s*\)", re.IGNORECASE)
    q = p2.sub(r"strftime(TRY_CAST(\1 AS DATE), \2)", q)

    # 3. Fix uncast date_trunc('month', col) -> date_trunc('month', TRY_CAST(col AS DATE))
    p3 = re.compile(r"date_trunc\s*\(\s*(['\"][a-z]+['\"])\s*,\s*(?!(?:TRY_CAST|CAST)\s*\()([a-zA-Z_][a-zA-Z0-9_\.\"]*)\s*\)", re.IGNORECASE)
    q = p3.sub(r"date_trunc(\1, TRY_CAST(\2 AS DATE))", q)
    return q


def md_table(columns: list[str], rows: list[list]) -> str:
    if not columns:
        return "(empty result)"
    head = "| " + " | ".join(_fmt(c) for c in columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    body = "\n".join("| " + " | ".join(_fmt(c) for c in row) + " |" for row in rows)
    return f"{head}\n{sep}\n{body}"


class ToolBox:
    def __init__(self, engine: DataEngine, session_id: str) -> None:
        self.engine = engine
        self.session_id = session_id
        self.export_dir = config.EXPORT_DIR / session_id
        self.export_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ tools
    def run_sql(self, query: str) -> dict:
        started = time.time()
        clean_query = _sanitize_duckdb_sql(query)
        try:
            df = self.engine.execute(clean_query)
        except Exception as e:  # surfaced to the LLM for self-correction
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        n = len(df)
        head = df.head(config.MAX_ROWS_TO_LLM)
        payload = {
            "ok": True,
            "sql": clean_query,
            "row_count": n,
            "columns": [str(c) for c in df.columns],
            "rows": head.astype(object).where(pd.notnull(head), None).values.tolist(),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        if n > config.MAX_ROWS_TO_LLM:
            fname = hashlib.sha1(query.encode()).hexdigest()[:10] + ".csv"
            (self.export_dir / fname).write_text(df.to_csv(index=False))
            payload["export"] = f"/api/exports/{self.session_id}/{fname}"
        return payload

    def profile_schema(self, table: str | None = None) -> dict:
        return {"ok": True, "tables": self.engine.profile(table)}

    def detect_anomalies(self, table: str | None = None, column: str | None = None, method: str = "iqr") -> dict:
        table = table or (self.engine.tables[0] if self.engine.tables else None)
        if not table:
            return {"ok": False, "error": "No table loaded."}
        try:
            df = self.engine.execute(f'SELECT * FROM "{table}" LIMIT 100000')
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if column and column not in df.columns:
            column = None
        result = analyse_dataframe(df, method or "iqr", [column] if column else None)
        result.update(ok=True, table=table)
        return result

    def build_chart(self, query: str, chart_type: str = "bar", x: str | None = None,
                    y: str | None = None, title: str | None = None) -> dict:
        r = self.run_sql(query)
        if not r["ok"]:
            return r
        df = pd.DataFrame(r["rows"], columns=r["columns"])
        if df.empty:
            return {"ok": False, "error": "Query returned no rows to chart."}
        if x and x not in df.columns:
            return {"ok": False, "error": f"Chart x column '{x}' is not in the query result."}
        if y and y not in df.columns:
            return {"ok": False, "error": f"Chart y column '{y}' is not in the query result."}
        if y and not pd.api.types.is_numeric_dtype(df[y]):
            return {"ok": False, "error": f"Chart y column '{y}' must be numeric."}
        if not y and df.select_dtypes(include="number").empty:
            return {"ok": False, "error": "Chart query must include a numeric column."}
        spec = charts.build_spec(df, chart_type, x, y, title)
        return {
            "ok": True,
            "chart_id": f"chart-{int(time.time() * 1000)}",
            "spec": spec,
            "row_count": r["row_count"],
            "sql": query,
        }

    # ------------------------------------------------------------- dispatch
    def dispatch(self, name: str, args: dict) -> tuple[str, list[dict]]:
        """Execute a tool by name. Returns (text_for_llm, client_events)."""
        args = args or {}
        if name == "run_sql":
            r = self.run_sql(args.get("query", ""))
            if not r["ok"]:
                return _sql_error_text(r), [{"type": "sql_error", "error": r["error"]}]
            ev = [{"type": "sql", "sql": r["sql"], "rows": r["row_count"], "export": r.get("export")}]
            return _sql_text(r), ev

        if name == "profile_schema":
            r = self.profile_schema(args.get("table"))
            return _schema_text(r), [{"type": "schema", "tables": r["tables"]}]

        if name == "detect_anomalies":
            r = self.detect_anomalies(args.get("table"), args.get("column"), args.get("method", "iqr"))
            if not r.get("ok"):
                return r.get("error", "anomaly detection failed"), []
            ev = {k: v for k, v in r.items() if k != "ok"}
            return _anomaly_text(r), [{"type": "anomaly", **ev}]

        if name == "build_chart":
            r = self.build_chart(args.get("query", ""), args.get("chart_type", "bar"),
                                 args.get("x"), args.get("y"), args.get("title"))
            if not r.get("ok"):
                return r.get("error", "chart failed"), []
            return (f"Chart created and displayed to the user "
                    f"({r['row_count']} data points)."), [{"type": "chart", "spec": r["spec"]}]

        return f"Unknown tool '{name}'. Available: run_sql, profile_schema, detect_anomalies, build_chart.", []

    def schema_digest(self) -> str:
        lines = []
        for t in self.engine.profile():
            safe_table = _untrusted_text(t["table"], 64)
            clean_cols = []
            for c in t["columns"]:
                c_name = _untrusted_text(c["name"], 64)
                c_type = _untrusted_text(c["type"], 32)
                clean_cols.append(f"{c_name} ({c_type})")
            cols = ", ".join(clean_cols)
            lines.append(f"- {safe_table} ({t['rows']} rows): {cols}")
        return "\n".join(lines) or "(no tables loaded)"


# --------------------------------------------------------------- LLM text
def _sql_text(r: dict) -> str:
    table = md_table(r["columns"], r["rows"])
    out = (
        f"[UNTRUSTED QUERY RESULT DATA]\n"
        f"Query OK — {r['row_count']} rows, returned in {r['elapsed_ms']} ms.\n"
        f"<query_data>\n{table}\n</query_data>"
    )
    if r.get("export"):
        out += (f"\n[SYSTEM: result had {r['row_count']} rows; only {len(r['rows'])} shown. "
                f"Full result exported for the user at {r['export']}]")
    return out


def _sql_error_text(r: dict) -> str:
    return (f"SQL ERROR: {r['error']}\n"
            "Read the error, fix the SQL (DuckDB dialect, correct table/column names), and retry.")


def _schema_text(r: dict) -> str:
    out = ["[UNTRUSTED SCHEMA PROFILE DATA]"]
    for t in r["tables"]:
        safe_table = _untrusted_text(t["table"], 64)
        lines = [f"Table {safe_table} ({t['rows']} rows):"]
        for c in t["columns"]:
            c_name = _untrusted_text(c["name"], 64)
            c_type = _untrusted_text(c["type"], 32)
            bit = f"  - {c_name} ({c_type}), {c.get('nulls', 0)} nulls"
            if "avg" in c and c.get("avg") is not None:
                bit += f", range {c.get('min')}–{c.get('max')}, avg {round(c['avg'], 2)}"
            elif c.get("top_values"):
                clean_tops = [
                    repr(_untrusted_text(v, 40))
                    for v in c["top_values"][:3]
                ]
                bit += f", top values: {', '.join(clean_tops)}"
            lines.append(bit)
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _anomaly_text(r: dict) -> str:
    if not r["columns"]:
        return "No numeric columns suitable for anomaly detection."
    out = [f"[UNTRUSTED ANOMALY DATA]\nAnomaly scan of '{_untrusted_text(r['table'], 64)}' using {r['method'].upper()}:"]
    for c in r["columns"]:
        lo = c.get("lower_bound")
        hi = c.get("upper_bound")
        if lo is not None and hi is not None:
            out.append(f"- {_untrusted_text(c['column'], 64)}: {c['count']} anomalies. Normal range "
                       f"{round(lo, 2)} to {round(hi, 2)} (mean {round(c['mean'], 2)}, "
                       f"std {round(c['std'], 2)}).")
        else:
            out.append(f"- {_untrusted_text(c['column'], 64)}: {c['count']} anomalies beyond 3 standard deviations "
                       f"(mean {round(c['mean'], 2)}, std {round(c['std'], 2)}).")
        if c["count"] and c.get("sample"):
            first = c["sample"][0]
            clean_first = {
                _untrusted_text(k, 32):
                _untrusted_text(v, 40)
                for k, v in list(first.items())[:6]
            }
            out.append(f"  Example flagged row: {clean_first}")
    return "\n".join(out)
