"""OpenRouter function-calling agent with self-correction.

Flow per user question:
  user message -> [LLM decides -> tool call -> tool result -> LLM ...] -> answer

Supports standard OpenAI-compatible tool calling over OpenRouter API.
"""
from __future__ import annotations

import json
import time
from typing import Generator

import httpx

from . import config
from .tools import ToolBox

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = """You are AskCSV, a meticulous AI data analyst. You analyse the user's CSV tables by writing DuckDB SQL and statistical checks, then explain the results in clear, concise business English.

Loaded tables and schemas:
{schema}

Operating rules:
1. The engine is DuckDB (in-process, read-only). Write DuckDB-compatible SQL.
2. DuckDB Date/Time rules:
   - DuckDB's strftime signature is `strftime(date_or_timestamp, format_string)`. Notice the DATE comes FIRST, and format string SECOND (e.g. `strftime(TRY_CAST(order_date AS DATE), '%Y-%m')`).
   - When extracting month/year from string date columns, always use: `strftime(TRY_CAST(date_column AS DATE), '%Y-%m')` or `date_trunc('month', TRY_CAST(date_column AS DATE))`.
3. NEVER use SELECT * without a LIMIT. Prefer aggregations (SUM, COUNT, AVG, MIN, MAX, GROUP BY). When listing rows, append LIMIT 20 unless the user asks for more.
4. Never calculate numbers yourself — always call run_sql for exact results and base every number in your answer on the returned table.
5. If run_sql returns an error, read the message, fix the query, and retry (at most 3 attempts) before explaining the failure to the user.
6. Call build_chart (with your own aggregated SQL query as the `query` argument) whenever a trend, comparison, distribution, or share would strengthen the answer.
7. For anomaly questions, call detect_anomalies and explain WHY values were flagged: the IQR bounds, how far outside they sit, and plausible business interpretations.
8. If unsure about a table or column name, call profile_schema first.
9. Keep answers short: lead with the answer, show the key numbers, then one line of reasoning. Never fabricate values.
10. Monetary figures are usually INR (₹). Format large numbers readably (e.g. ₹12.4 lakh or ₹1.2 crore is fine in an Indian context).
"""


def stream_tokens(text: str, words_per_chunk: int = 6):
    """Split text into small token chunks for a live typing effect over SSE."""
    words = text.split(" ")
    for i in range(0, len(words), words_per_chunk):
        yield {"type": "token", "text": " ".join(words[i:i + words_per_chunk]) + " "}
        time.sleep(0.012)


def _openrouter_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "profile_schema",
                "description": "Get schema and column statistics for loaded CSV tables.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table": {
                            "type": "string",
                            "description": "Optional table name; omit for all tables",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_sql",
                "description": (
                    "Execute a read-only DuckDB SQL query and get up to 20 rows back "
                    "as a markdown table. Use this for every number you report."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "A single DuckDB SELECT statement",
                        }
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "detect_anomalies",
                "description": "Statistical anomaly detection (IQR or z-score) on numeric columns.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "column": {
                            "type": "string",
                            "description": "Optional: a single numeric column",
                        },
                        "method": {
                            "type": "string",
                            "enum": ["iqr", "zscore"],
                            "description": "Default iqr",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "build_chart",
                "description": (
                    "Build a chart and show it to the user. Provide an aggregated SQL query "
                    "whose result is the data to plot, plus axis columns and a chart type."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Aggregated, read-only DuckDB SELECT",
                        },
                        "chart_type": {
                            "type": "string",
                            "enum": ["bar", "line", "area", "scatter", "pie", "donut"],
                        },
                        "x": {
                            "type": "string",
                            "description": "Column name for x / labels",
                        },
                        "y": {
                            "type": "string",
                            "description": "Column name for y / values",
                        },
                        "title": {"type": "string"},
                    },
                    "required": ["query", "chart_type", "x", "y"],
                },
            },
        },
    ]


class OpenRouterAgent:
    def __init__(self, engine, session_id: str, api_key: str | None = None, model: str | None = None) -> None:
        self.tb = ToolBox(engine, session_id)
        self.api_key = api_key or config.OPENROUTER_API_KEY
        self.model = model or config.OPENROUTER_MODEL
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    def chat(self, contents: list, message: str) -> Generator[dict, None, None]:
        """Generator of SSE events. Mutates `contents` in place (session messages)."""
        # Ensure system message is first if not already present
        if not contents or contents[0].get("role") != "system":
            system = SYSTEM_PROMPT.format(schema=self.tb.schema_digest())
            contents.insert(0, {"role": "system", "content": system})
        else:
            # Update schema digest in system prompt
            contents[0]["content"] = SYSTEM_PROMPT.format(schema=self.tb.schema_digest())

        contents.append({"role": "user", "content": message})
        tools = _openrouter_tools()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/askcsv",
            "X-Title": "AskCSV AI Data Analyst",
            "Content-Type": "application/json",
        }

        for _ in range(config.MAX_TOOL_STEPS):
            payload = {
                "model": self.model,
                "messages": contents,
                "tools": tools,
                "temperature": 0.2,
            }

            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(OPENROUTER_URL, headers=headers, json=payload)
            except Exception as e:
                yield {"type": "error", "detail": f"OpenRouter connection error: {e}"}
                return

            if resp.status_code != 200:
                err_body = resp.text
                try:
                    err_json = resp.json()
                    err_body = err_json.get("error", {}).get("message", err_body)
                except Exception:
                    pass
                yield {"type": "error", "detail": f"OpenRouter API error ({resp.status_code}): {err_body}"}
                return

            res_data = resp.json()
            choices = res_data.get("choices") or []
            if not choices:
                yield {"type": "error", "detail": "Empty response from OpenRouter."}
                return

            msg = choices[0].get("message") or {}
            tool_calls = msg.get("tool_calls") or []

            if tool_calls:
                contents.append(msg)
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    try:
                        fn_args = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        fn_args = {}

                    yield {"type": "status", "detail": f"Running {fn_name}…"}
                    try:
                        text, events = self.tb.dispatch(fn_name, fn_args)
                    except Exception as e:
                        text, events = f"Tool error: {e}", []

                    for ev in events:
                        yield ev
                    yield {"type": "status", "detail": ""}

                    contents.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": fn_name,
                        "content": text,
                    })
                continue

            # Final answer text
            answer_text = msg.get("content") or ""
            if not answer_text:
                yield {"type": "error", "detail": "Model returned no content."}
                return

            contents.append(msg)
            yield from stream_tokens(answer_text)
            return

        yield {"type": "error", "detail": "Agent exceeded its tool-call budget for this question."}
