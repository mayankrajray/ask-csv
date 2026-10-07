"""FastAPI application — Multi-file CSV/XLSX/XLS upload, Chat (SSE),
Spreadsheet Preview, Auto-Dashboard, Data Quality, Forecasting, Executive Reports & Observability.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from fastapi.middleware.cors import CORSMiddleware

from . import analytics, config
from .agent import GeminiAgent
from .demo_agent import DemoAgent
from .engine import DataEngine
from .openrouter_agent import OpenRouterAgent

app = FastAPI(title="AskCSV", version="1.0.0",
              description="An AI data analyst you can talk to.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: dict[str, dict] = {}


# ------------------------------------------------------------------ helpers
def _sanitize_table(stem: str) -> str:
    t = re.sub(r"[^0-9a-zA-Z_]+", "_", stem).strip("_").lower()
    if not t:
        t = "table"
    if not t[0].isalpha():
        t = "t_" + t
    return t


def _create_agent(engine: DataEngine, sid: str, provider: str | None = None, model: str | None = None):
    prov = (provider or config.mode()).lower()
    if prov == "openrouter" and (config.OPENROUTER_API_KEY or provider == "openrouter"):
        try:
            return OpenRouterAgent(engine, sid, model=model or config.OPENROUTER_MODEL)
        except Exception:
            return DemoAgent(engine, sid)
    elif prov == "gemini" and (config.GEMINI_API_KEY or provider == "gemini"):
        try:
            return GeminiAgent(engine, sid)
        except Exception:
            return DemoAgent(engine, sid)
    return DemoAgent(engine, sid)


def _new_session(provider: str | None = None, model: str | None = None) -> dict:
    if len(sessions) >= config.SESSION_LIMIT:
        oldest = min(sessions.values(), key=lambda s: s["created"])
        sessions.pop(oldest["id"], None)
    sid = uuid.uuid4().hex
    engine = DataEngine()
    agent = _create_agent(engine, sid, provider, model)
    sessions[sid] = {
        "id": sid,
        "engine": engine,
        "agent": agent,
        "provider": provider or config.mode(),
        "model": model or config.active_model(),
        "contents": [],
        "query_logs": [],
        "created": time.time(),
    }
    return sessions[sid]


def _quality(engine: DataEngine, table: str) -> dict:
    df = engine.execute(f'SELECT * FROM "{table}"')
    return {
        "rows": len(df),
        "duplicates": int(df.duplicated().sum()),
        "null_cells": int(df.isna().sum().sum()),
        "constant_columns": [c for c in df.columns if df[c].nunique(dropna=False) <= 1],
    }


def _table_payload(engine: DataEngine) -> list[dict]:
    out = []
    for t in engine.profile():
        t = dict(t)
        t["quality"] = _quality(engine, t["table"])
        out.append(t)
    return out


# --------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {
        "mode": config.mode(),
        "model": config.active_model(),
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "openrouter_configured": bool(config.OPENROUTER_API_KEY),
    }


class SwitchConfigBody(BaseModel):
    provider: str                      # "gemini", "openrouter", "demo"
    model: Optional[str] = None        # e.g. "gemini-3.6-flash", "openai/gpt-4o-mini", etc.
    api_key: Optional[str] = None      # Optional key update
    session_id: Optional[str] = None   # Update current session agent if provided


@app.get("/api/config")
def get_config():
    return {
        "active_provider": config.mode(),
        "active_model": config.active_model(),
        "gemini": {
            "configured": bool(config.GEMINI_API_KEY),
            "model": config.GEMINI_MODEL,
            "models": ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-2.5-pro", "gemini-2.5-flash"],
        },
        "openrouter": {
            "configured": bool(config.OPENROUTER_API_KEY),
            "model": config.OPENROUTER_MODEL,
            "models": [
                "openai/gpt-4o-mini",
                "openai/gpt-4o",
                "anthropic/claude-sonnet-5.5",
                "deepseek/deepseek-chat",
                "meta-llama/llama-3.3-70b-instruct",
                "google/gemini-2.5-flash",
                "~openai/gpt-sol-latest",
            ],
        },
    }


@app.post("/api/config/switch")
def switch_config(body: SwitchConfigBody):
    prov = body.provider.lower().strip()
    if prov not in ("gemini", "openrouter", "demo", "auto"):
        raise HTTPException(400, "Invalid provider. Choose 'gemini', 'openrouter', or 'demo'.")

    if prov == "gemini" and not (body.api_key or config.GEMINI_API_KEY):
        raise HTTPException(400, "No Gemini API key configured. Add GEMINI_API_KEY to .env or enter a key.")
    if prov == "openrouter" and not (body.api_key or config.OPENROUTER_API_KEY):
        raise HTTPException(400, "No OpenRouter API key configured. Add OPENROUTER_API_KEY to .env or enter a key.")

    if prov == "gemini":
        config.LLM_PROVIDER = "gemini"
        if body.model:
            config.GEMINI_MODEL = body.model.strip()
        if body.api_key:
            config.GEMINI_API_KEY = body.api_key.strip()
    elif prov == "openrouter":
        config.LLM_PROVIDER = "openrouter"
        if body.model:
            config.OPENROUTER_MODEL = body.model.strip()
        if body.api_key:
            config.OPENROUTER_API_KEY = body.api_key.strip()
    elif prov == "demo":
        config.LLM_PROVIDER = "demo"

    # If session_id is active, update the session's agent immediately
    if body.session_id and body.session_id in sessions:
        sess = sessions[body.session_id]
        if sess.get("provider") != prov:
            sess["contents"] = []
        sess["agent"] = _create_agent(sess["engine"], body.session_id, prov, body.model)
        sess["provider"] = prov
        sess["model"] = body.model or config.active_model()

    return {
        "ok": True,
        "mode": config.mode(),
        "model": config.active_model(),
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "openrouter_configured": bool(config.OPENROUTER_API_KEY),
    }


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No files received.")
    sess, created = None, []
    for f in files:
        name = f.filename or ""
        ext = Path(name).suffix.lower()
        if ext not in (".csv", ".xlsx", ".xls"):
            raise HTTPException(400, f"'{name}' is not supported. Only .csv, .xlsx, and .xls files are allowed.")
        data = await f.read()
        if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(400, f"'{name}' exceeds the {config.MAX_UPLOAD_MB} MB limit.")
        if sess is None:
            sess = _new_session()
        table_base = _sanitize_table(Path(name).stem)
        path = config.UPLOAD_DIR / f"{sess['id']}_{table_base}{ext}"
        path.write_bytes(data)
        try:
            loaded = sess["engine"].load_file(table_base, str(path))
            created.extend(loaded)
        except Exception as e:
            raise HTTPException(400, f"Could not parse '{name}': {e}")
    return {"session_id": sess["id"], "tables": _table_payload(sess["engine"])}


@app.post("/api/sample")
def load_sample():
    p = config.DATA_DIR / "sales.csv"
    if not p.exists():
        raise HTTPException(500, "Sample dataset missing from data/.")
    sess = _new_session()
    sess["engine"].load_csv("sales", str(p))
    return {"session_id": sess["id"], "tables": _table_payload(sess["engine"])}


class ChatBody(BaseModel):
    session_id: str
    message: str
    provider: Optional[str] = None
    model: Optional[str] = None


@app.post("/api/chat")
def chat(body: ChatBody):
    sess = sessions.get(body.session_id)
    if not sess:
        raise HTTPException(404, "Unknown session — upload a dataset first.")
    if not sess["engine"].tables:
        raise HTTPException(400, "This session has no tables.")

    # Update agent dynamically if provider / model passed in chat request
    if body.provider and (body.provider != sess.get("provider") or (body.model and body.model != sess.get("model"))):
        if body.provider != sess.get("provider"):
            sess["contents"] = []
        sess["agent"] = _create_agent(sess["engine"], body.session_id, body.provider, body.model)
        sess["provider"] = body.provider
        sess["model"] = body.model or config.active_model()

    start_time = time.time()

    def gen():
        try:
            for ev in sess["agent"].chat(sess["contents"], body.message):
                # Log query if SQL event
                if ev.get("type") == "sql":
                    sess.setdefault("query_logs", []).append({
                        "timestamp": time.strftime("%H:%M:%S"),
                        "sql": ev.get("sql"),
                        "rows": ev.get("rows"),
                        "latency_ms": int((time.time() - start_time) * 1000),
                        "status": "success",
                    })
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:  # never kill the stream mid-answer
            yield f"data: {json.dumps({'type': 'error', 'detail': f'{type(e).__name__}: {e}'})}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class NewChatBody(BaseModel):
    session_id: str


@app.post("/api/chat/new")
def new_chat(body: NewChatBody):
    sess = sessions.get(body.session_id)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    sess["contents"] = []
    return {"ok": True, "session_id": body.session_id, "message": "Conversation context reset."}


@app.get("/api/schema/{sid}/{table}")
def schema(sid: str, table: str):
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    try:
        return {"ok": True, "profile": sess["engine"].profile(table)[0]}
    except Exception:
        raise HTTPException(404, f"Unknown table '{table}'.")


@app.get("/api/preview/{sid}/{table}")
def preview(sid: str, table: str, limit: int = 500, offset: int = 0):
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    if table not in sess["engine"].tables:
        raise HTTPException(404, f"Unknown table '{table}'.")
    try:
        lim = 1000 if limit <= 0 else min(limit, 5000)
        df = sess["engine"].execute(f'SELECT * FROM "{table}" LIMIT {lim} OFFSET {max(0, offset)}')
        total_rows = int(sess["engine"].con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        clean_rows = df.astype(object).where(pd.notnull(df), None).values.tolist()
        schema = sess["engine"].schema(table)[0]["columns"]
        return {
            "ok": True,
            "table": table,
            "total_rows": total_rows,
            "columns": list(df.columns),
            "schema": schema,
            "rows": clean_rows,
        }
    except Exception as e:
        raise HTTPException(500, f"Error generating preview: {e}")


# ------------------------------------------------------------------ Analytics Endpoints
@app.get("/api/dashboard/{sid}/{table}")
def api_dashboard(sid: str, table: str):
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    try:
        return analytics.generate_dashboard(sess["engine"], table)
    except Exception as e:
        raise HTTPException(500, f"Dashboard error: {e}")


@app.get("/api/quality/{sid}/{table}")
def api_quality(sid: str, table: str):
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    try:
        return analytics.audit_data_quality(sess["engine"], table)
    except Exception as e:
        raise HTTPException(500, f"Quality audit error: {e}")


@app.get("/api/forecast/{sid}/{table}")
def api_forecast(sid: str, table: str, date_col: Optional[str] = None,
                 metric_col: Optional[str] = None, periods: int = 6):
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    try:
        return analytics.forecast_metric(sess["engine"], table, date_col, metric_col, periods)
    except Exception as e:
        raise HTTPException(500, f"Forecasting error: {e}")


@app.get("/api/report/{sid}/{table}")
def api_report(sid: str, table: str):
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    try:
        return analytics.generate_report(sess["engine"], table)
    except Exception as e:
        raise HTTPException(500, f"Report generation error: {e}")


@app.get("/api/logs/{sid}")
def api_logs(sid: str):
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    return {
        "ok": True,
        "session_id": sid,
        "provider": sess.get("provider", config.mode()),
        "model": sess.get("model", config.active_model()),
        "created": sess.get("created"),
        "tables": sess["engine"].tables,
        "logs": sess.get("query_logs", []),
    }


# Static: downloadable query exports, then the frontend itself (must be last).
app.mount("/api/exports", StaticFiles(directory=config.EXPORT_DIR), name="exports")
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
