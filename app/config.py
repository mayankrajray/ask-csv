"""Central configuration (env-driven, safe defaults)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
EXPORT_DIR = BASE_DIR / "exports"
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()  # default to "openrouter"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()

# Safety limits
MAX_ROWS_TO_LLM = int(os.getenv("MAX_ROWS_TO_LLM", "20"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_TOOL_STEPS = 8          # agent tool-call budget per question
SESSION_LIMIT = 200         # in-memory sessions kept

for _d in (UPLOAD_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def mode() -> str:
    """'openrouter' or 'gemini' when configured, otherwise 'demo'."""
    if LLM_PROVIDER == "openrouter" and OPENROUTER_API_KEY:
        return "openrouter"
    if LLM_PROVIDER == "gemini" and GEMINI_API_KEY:
        return "gemini"
    if OPENROUTER_API_KEY:
        return "openrouter"
    if GEMINI_API_KEY:
        return "gemini"
    return "demo"


def active_model() -> str:
    m = mode()
    if m == "openrouter":
        return OPENROUTER_MODEL
    if m == "gemini":
        return GEMINI_MODEL
    return "offline-demo"
