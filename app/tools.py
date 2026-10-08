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
            export_path = self.export_dir / fname
            try:
                export_path.write_text(df.to_csv(index=False))
            except Exception:
                export_path.unlink(missing_ok=True)
                return {"ok": False, "error": "Query results were found, but the CSV export could not be created."}
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
        try:
            spec = charts.build_spec(df, chart_type, x, y, title)
        except Exception:
            return {"ok": False, "error": "Chart generation failed for the query result."}
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
        if not isinstance(name, str) or not name:
            return self._tool_error("Tool call is missing a valid function name.")
        if not isinstance(args, dict):
            return self._tool_error("Tool arguments must be an object.")
        if any(not isinstance(key, str) for key in args):
            return self._tool_error("Tool argument names must be strings.")
        try:
            validation_error = self._validate_tool_args(name, args)
        except Exception:
            return self._tool_error("Tool arguments could not be validated against the loaded dataset.")
        if validation_error:
            return self._tool_error(validation_error)

        try:
            if name == "run_sql":
                r = self.run_sql(args["query"])
                if not r["ok"]:
                    return _sql_error_text(r), [{"type": "sql_error", "error":
                            "The query could not run. Check its syntax, table names, and columns."}]
                ev = [{"type": "sql", "sql": r["sql"], "rows": r["row_count"], "export": r.get("export")}]
                return _sql_text(r), ev

            if name == "profile_schema":
                r = self.profile_schema(args.get("table"))
                return _schema_text(r), [{"type": "schema", "tables": r["tables"]}]

            if name == "detect_anomalies":
                r = self.detect_anomalies(args.get("table"), args.get("column"), args.get("method", "iqr"))
                if not r.get("ok"):
                    return self._tool_error(r.get("error", "Anomaly detection failed."),
                                            "Anomaly detection could not complete for this dataset.")
                ev = {k: v for k, v in r.items() if k != "ok"}
                return _anomaly_text(r), [{"type": "anomaly", **ev}]

            if name == "build_chart":
                r = self.build_chart(args["query"], args["chart_type"], args["x"], args["y"], args.get("title"))
                if not r.get("ok"):
                    return self._tool_error(r.get("error", "Chart generation failed."),
                                            "The chart could not be created. Check the query and chart columns.")
                return (f"Chart created and displayed to the user "
                        f"({r['row_count']} data points)."), [{"type": "chart", "spec": r["spec"]}]

            return self._tool_error(f"Unknown tool '{name}'. Available: run_sql, profile_schema, detect_anomalies, build_chart.")
        except Exception as exc:
            return self._tool_error(f"Tool '{name}' failed ({type(exc).__name__}). Check the input and try again.")

    def _tool_error(self, detail: str, user_detail: str | None = None) -> tuple[str, list[dict]]:
        return detail, [{"type": "error", "detail": user_detail or detail}]

    def _validate_tool_args(self, name: str, args: dict) -> str | None:
        allowed = {
            "run_sql": {"query"},
            "profile_schema": {"table"},
            "detect_anomalies": {"table", "column", "method"},
            "build_chart": {"query", "chart_type", "x", "y", "title"},
        }
        if name not in allowed:
            return "Unknown tool name. Use an available analysis tool."
        unexpected = set(args) - allowed[name]
        if unexpected:
            return f"Unexpected argument for {name}. Remove unsupported fields and try again."

        if name == "run_sql":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return "run_sql requires a non-empty string 'query'."
        elif name == "profile_schema":
            table = args.get("table")
            if table is not None and not isinstance(table, str):
                return "profile_schema 'table' must be a string."
            if table and table not in self.engine.tables:
                return "Unknown table. Check the loaded dataset schema."
        elif name == "detect_anomalies":
            table, column, method = args.get("table"), args.get("column"), args.get("method", "iqr")
            if table is not None and not isinstance(table, str):
                return "detect_anomalies 'table' must be a string."
            if column is not None and not isinstance(column, str):
                return "detect_anomalies 'column' must be a string."
            if not isinstance(method, str) or method.lower() not in ("iqr", "zscore"):
                return "detect_anomalies 'method' must be 'iqr' or 'zscore'."
            if table and table not in self.engine.tables:
                return "Unknown table. Check the loaded dataset schema."
            if column:
                tables = [table] if table else self.engine.tables
                if not any(column in [c["name"] for c in self.engine.schema(t)[0]["columns"]]
                           for t in tables):
                    return "Unknown column. Check the selected table schema."
        elif name == "build_chart":
            for key in ("query", "chart_type", "x", "y"):
                if not isinstance(args.get(key), str) or not args[key].strip():
                    return f"build_chart requires a non-empty string '{key}'."
            if args["chart_type"].lower() not in charts.VALID_TYPES:
                return "Unsupported chart type. Choose a supported chart type."
            if args.get("title") is not None and not isinstance(args["title"], str):
                return "build_chart 'title' must be a string."
        return None

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
