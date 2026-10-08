"""DuckDB data engine: loads CSVs as views, profiles them, and executes
strictly read-only SQL.

Design notes
-----------
* CSVs are exposed as in-process DuckDB VIEWS (no import step, no DB server).
* Only the schema/profile ever reaches the LLM — never raw rows.
* validate_readonly() is the hard boundary between model-generated code and
  the engine: single statement, SELECT/WITH/DESCRIBE/EXPLAIN/SHOW only,
  and a forbidden-keyword denylist.
"""
from __future__ import annotations

import pathlib
import re

import duckdb
import pandas as pd

NUMERIC_HINTS = ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "HUGEINT")

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
# One alternation so strings and quoted identifiers are consumed in a single left-to-right
# pass; chained substitutions can be desynchronised by a quote inside the other construct.
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'" + "|" + r'"(?:[^"]|"")*"')
_FORBIDDEN_RE = re.compile(
    r"\b(drop|delete|update|insert|create|alter|attach|detach|replace|copy|"
    r"export|import|pragma|install|load|set|call|checkpoint|vacuum|analyze)\b",
    re.IGNORECASE,
)
# File / URL readers: blocked here and again by DuckDB's enable_external_access=false.
_FILE_ACCESS_RE = re.compile(
    r"\b(read_\w+|glob|\w+_scan|sniff_csv|duckdb_\w+)\s*\(|\bfrom\s+'|\bjoin\s+'",
    re.IGNORECASE,
)
_ALLOWED_STARTS = ("select", "with", "describe", "show", "explain")


def validate_readonly(query: str) -> str:
    """Validate that a query is a single read-only statement. Raises ValueError otherwise."""
    q = _COMMENT_RE.sub(" ", query or "").strip().rstrip(";").strip()
    if not q:
        raise ValueError("Empty SQL query.")
    # Inspect structure with literals and quoted identifiers blanked out, so a value such as
    # 'replace' or a column called "set" is not mistaken for a keyword.
    bare = _LITERAL_RE.sub(lambda m: "''" if m.group(0)[0] == "'" else '""', q)
    if ";" in bare:
        raise ValueError("Multiple SQL statements are not allowed.")
    first = bare.split(None, 1)[0].lower()
    if first not in _ALLOWED_STARTS:
        raise ValueError(f"Only read-only queries are allowed (statement starts with '{first.upper()}').")
    m = _FORBIDDEN_RE.search(bare)
    if m:
        raise ValueError(f"Forbidden keyword '{m.group(0).upper()}' in query.")
    if _FILE_ACCESS_RE.search(q):
        raise ValueError("Reading files or URLs from SQL is not allowed.")
    # Second opinion from DuckDB's own parser: exactly one statement, and it must be a query.
    try:
        stmts = duckdb.extract_statements(q)
    except duckdb.Error as e:
        raise ValueError(f"SQL could not be parsed: {e}") from None
    if len(stmts) != 1 or stmts[0].type not in (
            duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN, duckdb.StatementType.RELATION):
        raise ValueError("Only a single read-only query is allowed.")
    return q


def _clean_dataframe_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Auto-detect if row 0, 1, or 2 contains actual column headers when columns are banner titles or 'Unnamed:'."""
    if df.empty:
        return df

    unnamed_cols = [c for c in df.columns if str(c).startswith("Unnamed:")]
    # If half or more columns are Unnamed, or if there is a single title banner column followed by Unnamed
    if len(unnamed_cols) >= len(df.columns) / 2 or (len(unnamed_cols) > 0 and len(unnamed_cols) >= len(df.columns) - 2):
        best_row_idx = None
        max_valid_headers = 0
        for i in range(min(6, len(df))):
            row_vals = df.iloc[i].dropna().astype(str).str.strip().tolist()
            if len(row_vals) > max_valid_headers and len(row_vals) >= 2:
                max_valid_headers = len(row_vals)
                best_row_idx = i

        if best_row_idx is not None and max_valid_headers > 1:
            new_header = [str(x).strip() if pd.notna(x) and str(x).strip() else f"col_{j+1}" for j, x in enumerate(df.iloc[best_row_idx])]
            df = df.iloc[best_row_idx + 1:].copy().reset_index(drop=True)
            df.columns = new_header

    clean_cols = []
    seen = {}
    for c in df.columns:
        name = re.sub(r"[^0-9a-zA-Z_ ]+", "_", str(c)).strip("_ ")[:64].rstrip("_ ")
        if not name or name.startswith("Unnamed"):
            name = "column"
        if name in seen:
            seen[name] += 1
            clean_cols.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            clean_cols.append(name)
    df.columns = clean_cols
    return df.dropna(how="all").reset_index(drop=True)


class DataEngine:
    """Per-session, in-process DuckDB connection over uploaded CSVs and Excel files."""

    def __init__(self) -> None:
        self.con = duckdb.connect(database=":memory:")
        self._tables: dict[str, str] = {}
        self._locked = False
        self._closed = False

    def close(self) -> None:
        """Release the session's in-memory DuckDB connection. Safe to call repeatedly."""
        if not self._closed:
            self.con.close()
            self._closed = True

    def _lock_down(self) -> None:
        """Once data is loaded, cut DuckDB off from the filesystem and network."""
        if self._locked:
            return
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")
        self._locked = True

    def require_table(self, table: str) -> str:
        if table not in self._tables:
            raise ValueError(f"Unknown table '{table}'. Loaded tables: {self.tables}")
        return table

    def _ensure_new_tables(self, names: list[str]) -> None:
        """Reject duplicate names instead of silently replacing an existing dataset."""
        existing = {name.casefold() for name in self._tables}
        seen = set()
        for name in names:
            key = name.casefold()
            if key in existing or key in seen:
                raise ValueError(f"Table '{name}' is already loaded.")
            seen.add(key)

    # -- loading ---------------------------------------------------------
    def load_csv(self, table: str, path: str) -> None:
        # Parse with pandas (never SQL file readers), tolerating ragged rows.
        self._ensure_new_tables([table])
        try:
            df = pd.read_csv(path)
        except Exception:
            df = pd.read_csv(path, engine="python", on_bad_lines="skip")
        cleaned_df = _clean_dataframe_headers(df)
        self.con.register(f"_df_{table}", cleaned_df)
        self.con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM "_df_{table}"')
        self.con.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchall()  # fail fast on bad CSV
        self._tables[table] = path
        self._lock_down()

    def load_excel(self, table_prefix: str, path: str) -> list[str]:
        """Load an Excel workbook (.xlsx, .xls). If multi-sheet, creates a table per sheet."""
        excel_data = pd.read_excel(path, sheet_name=None)
        expected_names = []
        for sheet_name in excel_data:
            if len(excel_data) == 1:
                expected_names.append(table_prefix)
            else:
                clean_sheet = re.sub(r"[^0-9a-zA-Z_]+", "_", str(sheet_name)).strip("_").lower()
                expected_names.append(f"{table_prefix}_{clean_sheet}" if clean_sheet else table_prefix)
        self._ensure_new_tables(expected_names)
        created_tables = []
        for sheet_name, df in excel_data.items():
            if df.empty:
                continue
            cleaned_df = _clean_dataframe_headers(df)
            if cleaned_df.empty:
                continue
            if len(excel_data) == 1:
                tname = table_prefix
            else:
                clean_sheet = re.sub(r"[^0-9a-zA-Z_]+", "_", str(sheet_name)).strip("_").lower()
                tname = f"{table_prefix}_{clean_sheet}" if clean_sheet else table_prefix
            self.con.register(f"_df_{tname}", cleaned_df)
            self.con.execute(f'CREATE OR REPLACE TABLE "{tname}" AS SELECT * FROM "_df_{tname}"')
            self._tables[tname] = path
            created_tables.append(tname)
        self._lock_down()
        if not created_tables:
            raise ValueError(f"Excel file '{path}' contains no data sheets.")
        return created_tables

    def load_file(self, table: str, path: str) -> list[str]:
        ext = pathlib.Path(path).suffix.lower()
        if ext in (".xlsx", ".xls"):
            return self.load_excel(table, path)
        else:
            self.load_csv(table, path)
            return [table]

    @property
    def tables(self) -> list[str]:
        return sorted(self._tables)

    # -- schema & profiling ----------------------------------------------
    def schema(self, table: str | None = None) -> list[dict]:
        out = []
        for t in ([table] if table else self.tables):
            if t not in self._tables:
                raise ValueError(f"Unknown table '{t}'. Loaded tables: {self.tables}")
            desc = self.con.execute(f'DESCRIBE "{t}"').fetchall()
            out.append({"table": t, "columns": [{"name": r[0], "type": r[1]} for r in desc]})
        return out

    def profile(self, table: str | None = None) -> list[dict]:
        """Column-level statistics (nulls, distinct, min/max, top values)."""
        results = []
        for t in ([table] if table else self.tables):
            self.require_table(t)
            n = self.con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            cols = []
            for c in self.schema(t)[0]["columns"]:
                name, typ = c["name"], c["type"]
                col: dict = {"name": name, "type": typ}
                if any(h in typ.upper() for h in NUMERIC_HINTS):
                    nn, nd, mn, mx, avg = self.con.execute(
                        f'SELECT COUNT("{name}"), COUNT(DISTINCT "{name}"), '
                        f'MIN("{name}"), MAX("{name}"), AVG("{name}") FROM "{t}"'
                    ).fetchone()
                    col.update(nulls=n - nn, distinct=nd, min=mn, max=mx, avg=avg)
                else:
                    nn, nd = self.con.execute(
                        f'SELECT COUNT("{name}"), COUNT(DISTINCT "{name}") FROM "{t}"'
                    ).fetchone()
                    top = self.con.execute(
                        f'SELECT "{name}", COUNT(*) c FROM "{t}" GROUP BY 1 ORDER BY c DESC LIMIT 3'
                    ).fetchall()
                    col.update(nulls=n - nn, distinct=nd, top_values=[str(r[0]) for r in top])
                cols.append(col)
            results.append({"table": t, "rows": n, "columns": cols})
        return results

    # -- execution --------------------------------------------------------
    def execute(self, query: str) -> pd.DataFrame:
        q = validate_readonly(query)
        return self.con.execute(q).df()
