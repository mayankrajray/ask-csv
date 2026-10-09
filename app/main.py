"""FastAPI application — Multi-file CSV/XLSX/XLS upload, Chat (SSE),
Spreadsheet Preview, Auto-Dashboard, Data Quality, Forecasting, Executive Reports & Observability.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
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
from .groq_agent import GroqAgent
from .openrouter_agent import OpenRouterAgent

@asynccontextmanager
async def lifespan(_app):
    removed = _cleanup_expired_storage()
    if removed:
        log.info("Removed storage for %d expired sessions", removed)
    yield


app = FastAPI(title="AskCSV", version="1.0.0",
              description="An AI data analyst you can talk to.", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

log = logging.getLogger("askcsv")
sessions: dict[str, dict] = {}


def _remove_session_storage(sid: str) -> None:
    """Remove this session's local files without following paths outside storage roots."""
    upload_root = config.UPLOAD_DIR.resolve()
    for path in config.UPLOAD_DIR.glob(f"{sid}_*"):
        try:
            if path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.resolve().is_relative_to(upload_root) and path.is_file():
                path.unlink(missing_ok=True)
        except (OSError, RuntimeError, ValueError):
            log.warning("Could not remove an upload file during session cleanup")

    export_path = config.EXPORT_DIR / sid
    try:
        if export_path.is_symlink():
            export_path.unlink(missing_ok=True)
        elif export_path.resolve().is_relative_to(config.EXPORT_DIR.resolve()) and export_path.is_dir():
            shutil.rmtree(export_path)
    except (OSError, RuntimeError, ValueError):
        log.warning("Could not remove exports during session cleanup")


def _cleanup_expired_storage(now: float | None = None) -> int:
    """Remove files for sessions older than the in-memory session retention window."""
    cutoff = (time.time() if now is None else now) - config.SESSION_STORAGE_RETENTION_HOURS * 3600
    candidates: dict[str, list[float]] = {}
    try:
        if config.UPLOAD_DIR.exists():
            for path in config.UPLOAD_DIR.iterdir():
                match = re.match(r"^([0-9a-f]{32})_", path.name)
                if match:
                    candidates.setdefault(match.group(1), []).append(path.stat().st_mtime)
        if config.EXPORT_DIR.exists():
            for path in config.EXPORT_DIR.iterdir():
                if re.fullmatch(r"[0-9a-f]{32}", path.name):
                    candidates.setdefault(path.name, []).append(path.stat().st_mtime)
    except OSError:
        log.warning("Could not inspect local storage for expired sessions")
        return 0

    removed = 0
    for sid, timestamps in candidates.items():
        if sid not in sessions and timestamps and max(timestamps) < cutoff:
            _remove_session_storage(sid)
            removed += 1
    return removed


def _drop_session(sid: str) -> None:
    """Forget an in-memory session and remove its owned local files."""
    sess = sessions.pop(sid, None)
    if not sess:
        return
    engine = sess.get("engine")
    contents = sess.get("contents")
    if isinstance(contents, list):
        contents.clear()
    query_logs = sess.get("query_logs")
    if isinstance(query_logs, list):
        query_logs.clear()
    sess["api_key"] = None
    sess["provider"] = None
    sess["model"] = None
    sess["agent"] = None
    sess["engine"] = None
    try:
        if engine is not None:
            engine.close()
    except Exception:
        pass
    _remove_session_storage(sid)


def _expire_idle_sessions(now: float | None = None) -> int:
    current = time.time() if now is None else now
    idle_seconds = config.SESSION_STORAGE_RETENTION_HOURS * 3600
    expired = [sid for sid, sess in sessions.items()
               if current - sess.get("last_seen", sess.get("created", current)) >= idle_seconds]
    for sid in expired:
        _drop_session(sid)
    _cleanup_expired_storage(now=current)
    return len(expired)


def _session_for(sid: str | None) -> dict | None:
    if not sid:
        return None
    _expire_idle_sessions()
    sess = sessions.get(sid)
    if sess:
        sess["last_seen"] = time.time()
    return sess


# ------------------------------------------------------------------ helpers
def _sanitize_table(stem: str) -> str:
    t = re.sub(r"[^0-9a-zA-Z_]+", "_", stem).strip("_").lower()
    if not t:
        t = "table"
    if not t[0].isalpha():
        t = "t_" + t
    return t[:64].rstrip("_") or "table"


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
    elif prov == "groq":
        key = api_key or config.GROQ_API_KEY
        if key:
            try:
                return GroqAgent(engine, sid, api_key=key, model=model or config.GROQ_MODEL)
            except Exception:
                return DemoAgent(engine, sid)
    return DemoAgent(engine, sid)


def _new_session(provider: str | None = None, model: str | None = None, api_key: str | None = None) -> dict:
    _expire_idle_sessions()
    while len(sessions) >= config.SESSION_LIMIT:
        oldest = min(sessions.values(), key=lambda s: s["created"])
        _drop_session(oldest["id"])
    sid = uuid.uuid4().hex
    engine = None
    try:
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
            "last_seen": time.time(),
        }
        return sessions[sid]
    except Exception:
        if sid in sessions:
            _drop_session(sid)
        else:
            if engine is not None:
                try:
                    engine.close()
                except Exception:
                    pass
            _remove_session_storage(sid)
        raise


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
        "groq_configured": bool(config.GROQ_API_KEY),
    }


class SwitchConfigBody(BaseModel):
    provider: str                      # "gemini", "openrouter", "groq", "demo"
    model: Optional[str] = None        # Provider-specific model ID
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
        "groq": {
            "configured": bool(config.GROQ_API_KEY),
            "model": config.GROQ_MODEL,
            "models": [config.GROQ_MODEL],
        },
    }


@app.post("/api/config/switch")
def switch_config(body: SwitchConfigBody):
    prov = body.provider.lower().strip()
    if body.api_key and not config.ALLOW_KEY_OVERRIDE:
        raise HTTPException(403, "Setting API keys at runtime is disabled on this server.")
    if prov not in ("gemini", "openrouter", "groq", "demo", "auto"):
        raise HTTPException(400, "Invalid provider. Choose 'gemini', 'openrouter', 'groq', or 'demo'.")

    sess = _session_for(body.session_id)

    # Check whether the requested provider has valid credentials
    if prov == "gemini":
        has_key = bool(body.api_key or (sess and sess.get("api_key") and sess.get("provider") == "gemini") or config.GEMINI_API_KEY)
        if not has_key:
            raise HTTPException(400, "No Gemini API key configured. Add GEMINI_API_KEY to .env or enter a key.")
    elif prov == "openrouter":
        has_key = bool(body.api_key or (sess and sess.get("api_key") and sess.get("provider") == "openrouter") or config.OPENROUTER_API_KEY)
        if not has_key:
            raise HTTPException(400, "No OpenRouter API key configured. Add OPENROUTER_API_KEY to .env or enter a key.")
    elif prov == "groq":
        has_key = bool(body.api_key or (sess and sess.get("api_key") and sess.get("provider") == "groq") or config.GROQ_API_KEY)
        if not has_key:
            raise HTTPException(400, "No Groq API key configured. Add GROQ_API_KEY to the environment.")

    if not sess:
        raise HTTPException(400, "A valid session_id is required to switch provider configuration.")

    # Provider and key overrides are strictly session-scoped. Build the new agent
    # before changing live session state so a failed switch leaves it usable.
    new_key = body.api_key.strip() if body.api_key else (
        sess.get("api_key") if sess.get("provider") == prov else None
    )
    new_model = body.model or (
        config.GEMINI_MODEL if prov == "gemini" else
        config.OPENROUTER_MODEL if prov == "openrouter" else
        config.GROQ_MODEL if prov == "groq" else "offline-demo"
    )
    try:
        new_agent = _create_agent(sess["engine"], body.session_id, prov, new_model, new_key)
    except Exception:
        raise HTTPException(503, "The requested provider could not be initialized; existing session settings were kept.") from None
    if sess.get("provider") != prov:
        sess["contents"].clear()
    sess["api_key"] = new_key
    sess["provider"] = prov
    sess["model"] = new_model
    sess["agent"] = new_agent

    mode = sess["provider"]
    active_model = sess["model"]
    gemini_configured = bool(config.GEMINI_API_KEY or (sess.get("provider") == "gemini" and sess.get("api_key")))
    openrouter_configured = bool(config.OPENROUTER_API_KEY or (sess.get("provider") == "openrouter" and sess.get("api_key")))
    groq_configured = bool(config.GROQ_API_KEY or (sess.get("provider") == "groq" and sess.get("api_key")))

    return {
        "ok": True,
        "mode": mode,
        "model": active_model,
        "gemini_configured": gemini_configured,
        "openrouter_configured": openrouter_configured,
        "groq_configured": groq_configured,
    }


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(400, "No files received.")
    if len(files) > config.MAX_FILES_PER_SESSION:
        raise HTTPException(413, f"A session can contain at most {config.MAX_FILES_PER_SESSION} uploaded files.")
    sess, created = None, []
    written_paths: list[Path] = []
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    max_session_bytes = config.MAX_SESSION_UPLOAD_MB * 1024 * 1024
    session_bytes = 0
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
            if any(table.casefold() == table_base.casefold() for table in sess["engine"].tables):
                raise HTTPException(400, "A table with this name is already loaded.")
            path = config.UPLOAD_DIR / f"{sess['id']}_{table_base}{ext}"

            # Path traversal prevention: must resolve strictly inside UPLOAD_DIR
            try:
                resolved_path = path.resolve()
                upload_dir_resolved = config.UPLOAD_DIR.resolve()
                if path.is_symlink() or path.exists() or not resolved_path.is_relative_to(upload_dir_resolved):
                    raise HTTPException(400, "Invalid upload path.")
            except Exception:
                raise HTTPException(400, "Invalid upload filename.") from None

            # Stream chunks to disk and enforce max size limit on cumulative streamed bytes
            total_bytes = 0
            file_too_large = False
            with open(path, "xb") as out_f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        file_too_large = True
                        break
                    session_bytes += len(chunk)
                    if session_bytes > max_session_bytes:
                        file_too_large = True
                        break
                    out_f.write(chunk)

            if file_too_large:
                path.unlink(missing_ok=True)
                raise HTTPException(413, "The upload exceeds the configured per-file or per-session size limit.")

            written_paths.append(path)

            try:
                loaded = sess["engine"].load_file(table_base, str(path))
                created.extend(loaded)
            except Exception as exc:
                path.unlink(missing_ok=True)
                if isinstance(exc, ValueError) and "already loaded" in str(exc).lower():
                    raise HTTPException(400, "A table with this name is already loaded.") from None
                raise HTTPException(400, "The uploaded file could not be parsed as a supported dataset.")
    except HTTPException:
        for p in written_paths:
            p.unlink(missing_ok=True)
        if sess and sess["id"] in sessions:
            _drop_session(sess["id"])
        raise
    except Exception:
        for p in written_paths:
            p.unlink(missing_ok=True)
        if sess and sess["id"] in sessions:
            _drop_session(sess["id"])
        raise HTTPException(500, "Upload processing failed. Check the file format and try again.")

    try:
        return {"session_id": sess["id"], "tables": _table_payload(sess["engine"])}
    except Exception:
        if sess and sess["id"] in sessions:
            _drop_session(sess["id"])
        raise HTTPException(500, "Upload processing failed while preparing the dataset summary.") from None


@app.post("/api/sample")
def load_sample():
    p = config.DATA_DIR / "sales.csv"
    if not p.exists():
        raise HTTPException(500, "Sample dataset missing from data/.")
    sess = None
    try:
        sess = _new_session()
        sess["engine"].load_csv("sales", str(p))
        return {"session_id": sess["id"], "tables": _table_payload(sess["engine"])}
    except Exception:
        if sess and sess["id"] in sessions:
            _drop_session(sess["id"])
        raise HTTPException(500, "The sample dataset could not be loaded.") from None


class ChatBody(BaseModel):
    session_id: str
    message: str
    provider: Optional[str] = None
    model: Optional[str] = None


@app.post("/api/chat")
def chat(body: ChatBody):
    sess = _session_for(body.session_id)
    if not sess:
        raise HTTPException(404, "Unknown session — upload a dataset first.")
    if not sess["engine"].tables:
        raise HTTPException(400, "This session has no tables.")

    # Update agent dynamically if provider / model passed in chat request
    if body.provider and (body.provider != sess.get("provider") or (body.model and body.model != sess.get("model"))):
        new_provider = body.provider.lower().strip()
        new_model = body.model or (config.GROQ_MODEL if new_provider == "groq" else config.active_model())
        provider_key = sess.get("api_key") if sess.get("provider") == new_provider else None
        try:
            new_agent = _create_agent(sess["engine"], body.session_id, new_provider, new_model, provider_key)
        except Exception:
            raise HTTPException(503, "The requested provider could not be initialized; existing session settings were kept.") from None
        if new_provider != sess.get("provider"):
            sess["contents"].clear()
        sess["agent"] = new_agent
        sess["provider"] = new_provider
        sess["model"] = new_model
        sess["api_key"] = provider_key

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
    sess = _session_for(body.session_id)
    if not sess:
        raise HTTPException(404, "Unknown session.")
    sess["contents"].clear()
    reset_conversation = getattr(sess["agent"], "reset_conversation", None)
    if callable(reset_conversation):
        reset_conversation()
    return {"ok": True, "session_id": body.session_id, "message": "Conversation context reset."}


@app.get("/api/schema/{sid}/{table}")
def schema(sid: str, table: str):
    engine = _engine_for(sid, table)
    return {"ok": True, "profile": engine.profile(table)[0]}


@app.get("/api/preview/{sid}/{table}")
def preview(sid: str, table: str, limit: int = 500, offset: int = 0):
    sess = _session_for(sid)
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
    sess = _session_for(sid)
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
    sess = _session_for(sid)
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


# Export downloads are served only for a live session and a generated flat filename.
@app.get("/api/exports/{sid}/{filename}")
def download_export(sid: str, filename: str):
    if not re.fullmatch(r"[0-9a-f]{32}", sid) or not _session_for(sid):
        raise HTTPException(404, "Export not found.")
    if not re.fullmatch(r"[0-9a-f]{32}\.csv", filename):
        raise HTTPException(404, "Export not found.")
    root = config.EXPORT_DIR.resolve()
    try:
        session_dir = (config.EXPORT_DIR / sid).resolve(strict=True)
        target = (session_dir / filename).resolve(strict=True)
        if ((config.EXPORT_DIR / sid).is_symlink()
                or not session_dir.is_relative_to(root) or not target.is_relative_to(session_dir)
                or not target.is_file()):
            raise HTTPException(404, "Export not found.")
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(404, "Export not found.") from None
    from fastapi.responses import FileResponse
    return FileResponse(target, filename=filename, media_type="text/csv")


# Static frontend mount must remain last so it cannot shadow API routes.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
