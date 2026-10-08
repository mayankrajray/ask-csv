"""FastAPI application — Multi-file CSV/XLSX/XLS upload, Chat (SSE),
Spreadsheet Preview, Auto-Dashboard, Data Quality, Forecasting, Executive Reports & Observability.
"""
from __future__ import annotations

import json
import logging
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
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

log = logging.getLogger("askcsv")
sessions: dict[str, dict] = {}


# ------------------------------------------------------------------ helpers
def _sanitize_table(stem: str) -> str:
    t = re.sub(r"[^0-9a-zA-Z_]+", "_", stem).strip("_").lower()
    if not t:
        t = "table"
    if not t[0].isalpha():
        t = "t_" + t
    return t


def _create_agent(engine: DataEngine, sid: str, provider: str | None = None, model: str | None = None, api_key: str | None = None):
    prov = (provider or config.mode()).lower()
    if prov == "openrouter":
        key = api_key or config.OPENROUTER_API_KEY
        if key:
            try:
                return OpenRouterAgent(engine, sid, api_key=key, model=model or config.OPENROUTER_MODEL)
            except Exception:
                return DemoAgent(engine, sid)
    elif prov == "gemini":
        key = api_key or config.GEMINI_API_KEY
        if key:
            try:
                return GeminiAgent(engine, sid, api_key=key, model=model or config.GEMINI_MODEL)
            except Exception:
                return DemoAgent(engine, sid)
    return DemoAgent(engine, sid)


def _new_session(provider: str | None = None, model: str | None = None, api_key: str | None = None) -> dict:
    if len(sessions) >= config.SESSION_LIMIT:
        oldest = min(sessions.values(), key=lambda s: s["created"])
        sessions.pop(oldest["id"], None)
    sid = uuid.uuid4().hex
    engine = DataEngine()
    agent = _create_agent(engine, sid, provider, model, api_key)
    sessions[sid] = {
        "id": sid,
        "engine": engine,
        "agent": agent,
        "provider": provider or config.mode(),
        "model": model or config.active_model(),
        "api_key": api_key,
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
    if body.api_key and not config.ALLOW_KEY_OVERRIDE:
        raise HTTPException(403, "Setting API keys at runtime is disabled on this server.")
    if prov not in ("gemini", "openrouter", "demo", "auto"):
        raise HTTPException(400, "Invalid provider. Choose 'gemini', 'openrouter', or 'demo'.")

    sess = sessions.get(body.session_id) if body.session_id else None

    # Check whether the requested provider has valid credentials
    if prov == "gemini":
        has_key = bool(body.api_key or (sess and sess.get("api_key") and sess.get("provider") == "gemini") or config.GEMINI_API_KEY)
        if not has_key:
            raise HTTPException(400, "No Gemini API key configured. Add GEMINI_API_KEY to .env or enter a key.")
    elif prov == "openrouter":
        has_key = bool(body.api_key or (sess and sess.get("api_key") and sess.get("provider") == "openrouter") or config.OPENROUTER_API_KEY)
        if not has_key:
            raise HTTPException(400, "No OpenRouter API key configured. Add OPENROUTER_API_KEY to .env or enter a key.")

    if not sess:
        raise HTTPException(400, "A valid session_id is required to switch provider configuration.")

    # Provider and key overrides are strictly session-scoped.
    # Never mutate module-level global configuration (config.LLM_PROVIDER, config.GEMINI_API_KEY, etc.)
    if sess:
        if sess.get("provider") != prov:
            sess["contents"] = []
        if body.api_key:
            sess["api_key"] = body.api_key.strip()
        sess["provider"] = prov
        sess["model"] = body.model or (
            config.GEMINI_MODEL if prov == "gemini" else config.OPENROUTER_MODEL if prov == "openrouter" else "offline-demo"
        )
        sess["agent"] = _create_agent(sess["engine"], body.session_id, prov, sess["model"], sess.get("api_key"))

        mode = sess["provider"]
        active_model = sess["model"]
        gemini_configured = bool(config.GEMINI_API_KEY or (sess.get("provider") == "gemini" and sess.get("api_key")))
        openrouter_configured = bool(config.OPENROUTER_API_KEY or (sess.get("provider") == "openrouter" and sess.get("api_key")))
    else:
        mode = prov if prov != "auto" else config.mode()
        active_model = body.model or (
            config.GEMINI_MODEL if prov == "gemini" else config.OPENROUTER_MODEL if prov == "openrouter" else "offline-demo"
        )
        gemini_configured = bool(config.GEMINI_API_KEY or (body.api_key and prov == "gemini"))
        openrouter_configured = bool(config.OPENROUTER_API_KEY or (body.api_key and prov == "openrouter"))

    return {
        "ok": True,
        "mode": mode,
        "model": active_model,
        "gemini_configured": gemini_configured,
        "openrouter_configured": openrouter_configured,
    }


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No files received.")
    sess, created = None, []
    written_paths: list[Path] = []
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    chunk_size = 1024 * 1024  # 1 MB chunks

    try:
        for f in files:
            name = f.filename or ""
            ext = Path(name).suffix.lower()
            if ext not in (".csv", ".xlsx", ".xls"):
                raise HTTPException(400, f"'{name}' is not supported. Only .csv, .xlsx, and .xls files are allowed.")

            if sess is None:
                sess = _new_session()

            table_base = _sanitize_table(Path(name).stem)
            path = config.UPLOAD_DIR / f"{sess['id']}_{table_base}{ext}"

            # Path traversal prevention: must resolve strictly inside UPLOAD_DIR
            try:
                resolved_path = path.resolve()
                upload_dir_resolved = config.UPLOAD_DIR.resolve()
                if not resolved_path.is_relative_to(upload_dir_resolved):
                    raise HTTPException(400, f"Invalid filename '{name}'.")
            except Exception:
                raise HTTPException(400, f"Invalid filename '{name}'.")

            # Stream chunks to disk and enforce max size limit on cumulative streamed bytes
            total_bytes = 0
            file_too_large = False
            with open(path, "wb") as out_f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        file_too_large = True
                        break
                    out_f.write(chunk)

            if file_too_large:
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"'{name}' exceeds the {config.MAX_UPLOAD_MB} MB limit.")

            written_paths.append(path)

            try:
                loaded = sess["engine"].load_file(table_base, str(path))
                created.extend(loaded)
            except Exception as e:
                path.unlink(missing_ok=True)
                raise HTTPException(400, f"Could not parse '{name}': {e}")
    except HTTPException:
        for p in written_paths:
            p.unlink(missing_ok=True)
        if sess and sess["id"] in sessions:
            sessions.pop(sess["id"], None)
        raise
    except Exception as e:
        for p in written_paths:
            p.unlink(missing_ok=True)
        if sess and sess["id"] in sessions:
            sessions.pop(sess["id"], None)
        raise HTTPException(500, f"Upload processing error: {e}")

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
        sess["agent"] = _create_agent(sess["engine"], body.session_id, body.provider, body.model, sess.get("api_key"))
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
        except Exception:  # never expose provider/engine exception text to the client
            yield f"data: {json.dumps({'type': 'error', 'detail': 'The analysis request failed unexpectedly. Your session is still available; please try again.'})}\n\n"
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
    engine = _engine_for(sid, table)
    return {"ok": True, "profile": engine.profile(table)[0]}


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
    except Exception:
        log.exception("Preview failed")
        raise HTTPException(500, "Preview failed. See server logs for details.")


def _engine_for(sid: str, table: str) -> DataEngine:
    sess = sessions.get(sid)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    if table not in sess["engine"].tables:
        raise HTTPException(404, f"Unknown table '{table}'.")
    return sess["engine"]


# ------------------------------------------------------------------ Analytics Endpoints
@app.get("/api/dashboard/{sid}/{table}")
def api_dashboard(sid: str, table: str):
    engine = _engine_for(sid, table)
    try:
        return analytics.generate_dashboard(engine, table)
    except Exception:
        log.exception("Dashboard failed")
        raise HTTPException(500, "Dashboard failed. See server logs for details.")


@app.get("/api/quality/{sid}/{table}")
def api_quality(sid: str, table: str):
    engine = _engine_for(sid, table)
    try:
        return analytics.audit_data_quality(engine, table)
    except Exception:
        log.exception("Quality audit failed")
        raise HTTPException(500, "Quality audit failed. See server logs for details.")


@app.get("/api/forecast/{sid}/{table}")
def api_forecast(sid: str, table: str, date_col: Optional[str] = None,
                 metric_col: Optional[str] = None, periods: int = 6):
    engine = _engine_for(sid, table)
    try:
        return analytics.forecast_metric(engine, table, date_col, metric_col, periods)
    except Exception:
        log.exception("Forecasting failed")
        raise HTTPException(500, "Forecasting failed. See server logs for details.")


@app.get("/api/report/{sid}/{table}")
def api_report(sid: str, table: str):
    engine = _engine_for(sid, table)
    try:
        return analytics.generate_report(engine, table)
    except Exception:
        log.exception("Report generation failed")
        raise HTTPException(500, "Report generation failed. See server logs for details.")


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
