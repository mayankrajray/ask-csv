"""Groq agent using AskCSV's local OpenAI-compatible tool-calling loop."""
from __future__ import annotations

from . import config
from .openrouter_agent import OpenRouterAgent
from .tools import ToolBox


GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqAgent(OpenRouterAgent):
    provider_name = "Groq"
    api_url = f"{GROQ_BASE_URL}/chat/completions"

    def __init__(self, engine, session_id: str, api_key: str | None = None, model: str | None = None) -> None:
        # Reuse the existing OpenAI-compatible tool schemas and execution loop,
        # while keeping Groq credentials and model defaults separate.
        self.tb = ToolBox(engine, session_id)
        self.api_key = api_key or config.GROQ_API_KEY
        self.model = model or config.GROQ_MODEL
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not configured.")

    def _request_headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
