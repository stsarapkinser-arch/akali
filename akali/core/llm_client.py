"""LLM-клиенты и проверка сети для Query Router.

Иерархия:
    check_internet()  →  True  →  GeminiClient (gemini-2.5-flash)
                      →  False →  OllamaClient (qwen2.5-coder:1.5b)

Оба клиента используют один жёсткий SYSTEM_PROMPT,
гарантирующий «чистую bash-команду» на выходе.
"""
from __future__ import annotations

import re
import socket
from typing import Optional

# ── Константы ────────────────────────────────────────────────
NETWORK_HOST = "8.8.8.8"
NETWORK_PORT = 53
NETWORK_TIMEOUT = 1.5          # сек; жёсткий лимит на N100

OLLAMA_MODEL = "qwen2.5-coder:1.5b"
GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = (
    "Ты — системный модуль трансляции текста в команды для Kali Linux 2026.2 "
    "(Wayland, KDE Plasma 6). Железо: Intel N100.\n"
    "ПРАВИЛА:\n"
    "1. Выдавай ТОЛЬКО чистую bash-команду.\n"
    "2. Никакого Markdown, никаких символов ```bash, никаких пояснений.\n"
    "3. Если команда открывает графическое приложение, ОБЯЗАТЕЛЬНО ставь в конце &.\n"
    "4. Для управления окнами используй только qdbus (xdotool не работает на Wayland).\n"
    "5. Никаких символов-разделителей: ; | && || > <\n"
    "Если не знаешь точную команду, выведи: echo error"
)

# Паттерн для зачистки markdown-оберток от LLM
_MD_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.MULTILINE)


def check_internet() -> bool:
    """Быстрая проверка сети через socket (без subprocess/ping).

    Пытается TCP-коннект к 8.8.8.8:53 с таймаутом NETWORK_TIMEOUT сек.
    """
    try:
        with socket.create_connection((NETWORK_HOST, NETWORK_PORT),
                                      timeout=NETWORK_TIMEOUT):
            return True
    except (OSError, socket.timeout):
        return False


def _strip_fences(raw: str) -> str:
    """Удаляет markdown-ограждения ```bash ... ``` и лишние пробелы."""
    cleaned = _MD_FENCE.sub("", raw).strip()
    # Берём только первую строку — LLM иногда даёт пояснения после
    first_line = cleaned.splitlines()[0].strip() if cleaned else ""
    return first_line


# ── Ollama (локальная модель) ─────────────────────────────────
class OllamaClient:
    """Запрос к локальной модели через ollama.chat()."""

    def __init__(self, model: str = OLLAMA_MODEL):
        self.model = model

    def query(self, text: str) -> Optional[str]:
        try:
            import ollama  # noqa: WPS433
            resp = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": text},
                ],
                options={"temperature": 0, "num_predict": 128},
            )
            raw = resp.message.content if hasattr(resp, "message") else str(resp)
            return _strip_fences(raw) or None
        except Exception:  # noqa: BLE001
            return None


# ── Gemini (облачная модель) ──────────────────────────────────
class GeminiClient:
    """Запрос к Google Gemini API через google-generativeai."""

    def __init__(self, api_key: str, model: str = GEMINI_MODEL):
        self._api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import google.generativeai as genai  # noqa: WPS433
                genai.configure(api_key=self._api_key)
                self._client = genai.GenerativeModel(
                    model_name=self.model,
                    system_instruction=SYSTEM_PROMPT,
                )
            except ImportError as e:
                raise RuntimeError(
                    f"google-generativeai не установлен: pip install google-generativeai. {e}"
                ) from e
        return self._client

    def query(self, text: str) -> Optional[str]:
        try:
            client = self._get_client()
            resp = client.generate_content(
                text,
                generation_config={"temperature": 0, "max_output_tokens": 128},
            )
            return _strip_fences(resp.text) or None
        except Exception:  # noqa: BLE001
            return None
