"""Offline demo agent — a deterministic, API-key-free fallback.

It recognises a fixed set of intents (the assignment's example questions) and
answers them by calling the SAME ToolBox as the real agent: the SQL, the
anomaly math and the charts are all real; only the narrative is templated.
This lets anyone `docker compose up` and use the app without an API key,
and keeps the test-suite meaningful without network access.
"""
from __future__ import annotations

from .agent import stream_tokens
from . import config
from .conversation import trim_messages
from .tools import ToolBox

DEMO_NOTE = ("\n\n*Offline demo mode — set `GEMINI_API_KEY` in `.env` to unlock "
             "full LLM reasoning over any question.*")

VALUE_KEYS = ("revenue", "amount", "sales", "total", "spend", "price", "qty", "quantity")


class DemoAgent:
    def __init__(self, engine, session_id: str) -> None:
        self.tb = ToolBox(engine, session_id)
        self.last_sql: str | None = None

    # ------------------------------------------------------------ helpers
    def _numeric_cols(self, table: str) -> list[str]:
        prof = self.tb.engine.profile(table)[0]
        return [c["name"] for c in prof["columns"] if "avg" in c]

    def _value_col(self, cols: list[str]) -> str | None:
        for key in VALUE_KEYS:
            for c in cols:
                if key in c.lower():
                    return c
        return None

    def _find(self, *needles: str, numeric: bool = False):
        """Find (table, column) where the column name contains all needles."""
        for t in self.tb.engine.tables:
            names = self._numeric_cols(t) if numeric else \
                [c["name"] for c in self.tb.engine.schema(t)[0]["columns"]]
            for c in names:
                low = c.lower()
                if all(n in low for n in needles):
                    return t, c
        return None, None

    # ------------------------------------------------------------ handler
    def chat(self, contents: list, message: str):
        trim_messages(contents, config.MAX_CONVERSATION_TURNS - 1)
        context = next((item.get("demo_context") for item in reversed(contents)
                        if isinstance(item, dict) and item.get("demo_context")), None)
        context = self._valid_context(context)
        outcome = {}
        contents.append({"role": "user", "content": message})
        m = " ".join(message.lower().split())
        words = set(m.split())
        yield {"type": "status", "detail": "Analysing…"}

        try:
            if context and any(phrase in m for phrase in ("how much", "what was", "what did")) and \
                    words.intersection({"it", "that", "they", "its", "they're"}):
                yield from self._region_total_followup(context)
            elif "anomal" in m:
                yield from self._anomalies()
            elif any(k in m for k in ("trend", "monthly", "by month", "over time", "growth")):
                yield from self._trend()
            elif ("sql" in m) or m.startswith("show me the query"):
                yield from self._sql_request()
            elif "top" in m and any(k in m for k in ("customer", "client", "buyer")):
                yield from self._top_customers()
            elif any(k in m for k in ("underperform", "worst", "lowest")):
                yield from self._underperformers()
            elif any(k in m for k in ("region", "area", "zone", "state")) and \
                    any(k in m for k in ("highest", "best", "most", "top", "revenue", "sales")):
                yield from self._best_region(outcome)
            else:
                yield from self._generic()
        except Exception:
            yield {"type": "error", "detail": "Offline analysis could not complete. Check the loaded data and try a supported question."}
        finally:
            if outcome.get("context"):
                context = outcome["context"]
            contents.append({"role": "assistant", "content": "", **(
                {"demo_context": context} if context else {})})

    def _valid_context(self, context):
        if not isinstance(context, dict) or context.get("kind") != "region_total":
            return None
        fields = ("table", "group_column", "metric_column", "entity")
        if any(not isinstance(context.get(field), str) or not context[field] for field in fields):
            return None
        table = context["table"]
        if table not in self.tb.engine.tables:
            return None
        try:
            columns = {column["name"] for column in self.tb.engine.schema(table)[0]["columns"]}
        except Exception:
            return None
        if context["group_column"] not in columns or context["metric_column"] not in columns:
            return None
        return {key: context[key] for key in fields} | {"kind": "region_total"}

    # ------------------------------------------------------------ answers
    def _run_chart(self, sql: str, chart_type: str, x: str, y: str, title: str):
        try:
            r = self.tb.run_sql(sql)
        except Exception:
            yield {"type": "error", "detail": "The analysis query could not be completed. Check the data schema and try again."}
            return
        if not r["ok"]:
            yield {"type": "error", "detail": "The analysis query could not be completed. Check the data schema and try again."}
            return
        self.last_sql = sql
        yield {"type": "sql", "sql": sql, "rows": r["row_count"], "export": r.get("export")}
        try:
            chart = self.tb.build_chart(sql, chart_type, x, y, title)
        except Exception:
            yield {"type": "error", "detail": "The chart could not be generated from the analysis result."}
            yield r
            return
        if chart.get("ok"):
            yield {"type": "chart", "spec": chart["spec"]}
        else:
            yield {"type": "error", "detail": "The chart could not be generated from the analysis result."}
        yield r

    def _trend(self):
        t, dcol = self._find("date")
        if not dcol:
            t, dcol = self._find("month")
        table, date_col = (t, dcol) if dcol else (None, None)
        vcol = self._value_col(self._numeric_cols(table)) if table else None
        if not table or not vcol:
            yield from self._generic()
            return
        sql = (f'SELECT date_trunc(\'month\', TRY_CAST("{date_col}" AS DATE)) AS month, '
               f'ROUND(SUM("{vcol}") / 100000.0, 2) AS "{vcol}_lakh" FROM "{table}" '
               f'GROUP BY 1 ORDER BY 1')
        gen = self._run_chart(sql, "line", "month", f"{vcol}_lakh", f"Monthly {vcol} trend (₹ lakh)")
        rows = None
        for ev in gen:
            if isinstance(ev, dict) and ev.get("row_count") is not None:
                rows = ev
                continue
            yield ev
        if not rows or not rows["rows"]:
            return
        first, last = rows["rows"][0], rows["rows"][-1]
        a, b = float(first[1]), float(last[1])
        direction = "up" if b >= a else "down"
        text = (f"Here is the monthly **{vcol}** trend for `{table}`, aggregated with DuckDB "
                f"(`date_trunc('month', …)` + `SUM`).\n\n"
                f"It starts at **₹{a:,.1f} lakh** in {str(first[0])[:7]} and ends at "
                f"**₹{b:,.1f} lakh** in {str(last[0])[:7]} — an overall **{direction}** movement "
                f"across the period. The SQL block above shows exactly how these numbers were computed."
                + DEMO_NOTE)
        yield from stream_tokens(text)

    def _best_region(self, outcome=None):
        table, rcol = self._find("region") or self._find("area") or self._find("zone")
        if not rcol:
            yield from self._generic()
            return
        vcol = self._value_col(self._numeric_cols(table))
        if not vcol:
            yield from self._generic()
            return
        sql = (f'SELECT "{rcol}", ROUND(SUM("{vcol}") / 100000.0, 2) AS total_lakh '
               f'FROM "{table}" GROUP BY 1 ORDER BY 2 DESC')
        gen = self._run_chart(sql, "bar", rcol, "total_lakh", f"Total {vcol} by {rcol} (₹ lakh)")
        rows = None
        for ev in gen:
            if isinstance(ev, dict) and ev.get("row_count") is not None:
                rows = ev
                continue
            yield ev
        if rows and rows["rows"]:
            top = rows["rows"][0]
            if outcome is not None:
                outcome["context"] = {"kind": "region_total", "table": table,
                                      "group_column": rcol, "metric_column": vcol,
                                      "entity": str(top[0])}
            text = (f"**{top[0]}** generated the highest {vcol}: **₹{float(top[1]):,.1f} lakh**, "
                    f"ahead of {', '.join(f'{r[0]} (₹{float(r[1]):,.1f} lakh)' for r in rows['rows'][1:3])}. "
                    f"This comes straight from a `GROUP BY {rcol}` + `SUM({vcol})` query in DuckDB — "
                    f"the SQL is shown above so you can verify it." + DEMO_NOTE)
            yield from stream_tokens(text)

    def _region_total_followup(self, context):
        table, group_col = context["table"], context["group_column"]
        metric_col, entity = context["metric_column"], context["entity"]
        q = lambda value: '"' + value.replace('"', '""') + '"'
        literal = "'" + entity.replace("'", "''") + "'"
        sql = (f'SELECT ROUND(SUM({q(metric_col)}) / 100000.0, 2) AS total_lakh '
               f'FROM {q(table)} WHERE {q(group_col)} = {literal}')
        try:
            r = self.tb.run_sql(sql)
        except Exception:
            yield {"type": "error", "detail": "The follow-up analysis could not be completed. Try another question."}
            return
        if not r["ok"]:
            yield {"type": "error", "detail": "The follow-up query could not be completed. Check the loaded data and try again."}
            return
        self.last_sql = sql
        yield {"type": "sql", "sql": sql, "rows": r["row_count"], "export": r.get("export")}
        yield r
        amount = r["rows"][0][0] if r.get("rows") else 0
        yield from stream_tokens(f"**{entity}** sold ₹{float(amount or 0):,.1f} lakh in total." + DEMO_NOTE)

    def _top_customers(self):
        table, ccol = self._find("customer") or self._find("client") or self._find("buyer")
        if not ccol:
            yield from self._generic()
            return
        vcol = self._value_col(self._numeric_cols(table))
        sql = (f'SELECT "{ccol}", ROUND(SUM("{vcol}") / 100000.0, 2) AS total_lakh '
               f'FROM "{table}" GROUP BY 1 ORDER BY 2 DESC LIMIT 5')
        gen = self._run_chart(sql, "bar", ccol, "total_lakh", f"Top 5 {ccol}s by {vcol} (₹ lakh)")
        rows = None
        for ev in gen:
            if isinstance(ev, dict) and ev.get("row_count") is not None:
                rows = ev
                continue
            yield ev
        if rows and rows["rows"]:
            listing = "\n".join(f"{i + 1}. **{r[0]}** — ₹{float(r[1]):,.1f} lakh"
                                for i, r in enumerate(rows["rows"]))
            text = (f"Here are your top five {ccol}s by total {vcol}:\n\n{listing}\n\n"
                    f"(SQL: `GROUP BY {ccol}` → `SUM({vcol})` → `ORDER BY … DESC LIMIT 5`.)" + DEMO_NOTE)
            yield from stream_tokens(text)

    def _underperformers(self):
        table, pcol = self._find("product") or self._find("item") or self._find("sku")
        if not pcol:
            yield from self._generic()
            return
        vcol = self._value_col(self._numeric_cols(table))
        sql = (f'SELECT "{pcol}", ROUND(SUM("{vcol}") / 100000.0, 2) AS total_lakh '
               f'FROM "{table}" GROUP BY 1 ORDER BY 2 ASC LIMIT 5')
        gen = self._run_chart(sql, "bar", pcol, "total_lakh", f"Bottom 5 {pcol}s by {vcol} (₹ lakh)")
        rows = None
        for ev in gen:
            if isinstance(ev, dict) and ev.get("row_count") is not None:
                rows = ev
                continue
            yield ev
        if rows and rows["rows"]:
            listing = ", ".join(str(r[0]) for r in rows["rows"])
            text = (f"The five weakest **{pcol}s** by total {vcol} are: {listing}. "
                    f"I ranked every {pcol} with `SUM({vcol}) GROUP BY {pcol}` and kept the bottom five — "
                    f"see the SQL above. Note that a low total can mean genuinely weak demand *or* fewer "
                    f"units sold; compare with average order value before acting on it." + DEMO_NOTE)
            yield from stream_tokens(text)

    def _anomalies(self):
        r = self.tb.detect_anomalies()
        if not r.get("ok"):
            yield {"type": "error", "detail": r.get("error", "anomaly detection failed")}
            return
        ev = {k: v for k, v in r.items() if k != "ok"}
        yield {"type": "anomaly", **ev}
        flagged = [c for c in r["columns"] if c["count"] > 0]
        if not flagged:
            yield from stream_tokens(f"I scanned every numeric column of `{r['table']}` with the IQR "
                                      f"method and found no values outside the normal range." + DEMO_NOTE)
            return
        lines = [f"I scanned all numeric columns of `{r['table']}` with the **IQR method** "
                 f"(flag anything below Q1 − 1.5×IQR or above Q3 + 1.5×IQR) and found:"]
        for c in flagged:
            ex = ""
            if c.get("sample"):
                s = c["sample"][0]
                ex = f" — for example a row where `{c['column']}` = {s.get(c['column'])}"
            lines.append(f"- **{c['column']}**: {c['count']} anomalous value(s), normal range "
                         f"{round(c['lower_bound'], 1):,} to {round(c['upper_bound'], 1):,}{ex}")
        lines.append("\nThese points sit far outside the middle 50% of the distribution, so they are "
                     "statistically unusual — worth a human look before treating them as real business signal.")
        yield from stream_tokens("\n".join(lines) + DEMO_NOTE)

    def _sql_request(self):
        sql = self.last_sql or (
            'SELECT region, SUM(revenue) AS total_revenue\nFROM sales\nGROUP BY region\nORDER BY total_revenue DESC;'
        )
        yield {"type": "sql", "sql": sql, "rows": None}
        yield from stream_tokens("Here is the SQL behind the analysis above. It is a read-only "
                                "DuckDB query — the app validates every query before execution "
                                "and rejects anything that isn't a single SELECT." + DEMO_NOTE)

    def _generic(self):
        names = ", ".join(f"`{t}`" for t in self.tb.engine.tables) or "no table"
        digest = self.tb.schema_digest()
        text = (f"I'm running in **offline demo mode**, so I currently recognise a fixed set of questions "
                f"about your data (loaded: {names}).\n\nTry one of:\n"
                f"- Which region generated the highest revenue?\n"
                f"- Show monthly sales trends\n"
                f"- What are the top five customers?\n"
                f"- Which products are underperforming?\n"
                f"- Detect anomalies in the dataset\n"
                f"- Generate SQL for this analysis\n\n"
                f"Your data: {digest}\n\n"
                f"With a `GEMINI_API_KEY` configured I can answer arbitrary questions, chain tools, "
                f"and reason about the results." + DEMO_NOTE)
        yield from stream_tokens(text)
