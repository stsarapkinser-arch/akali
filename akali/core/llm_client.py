"""LLM-клиенты и проверка сети для Query Router.

Иерархия:
    check_internet()  →  True  →  GeminiClient (gemini-2.5-flash)
                      →  False →  OllamaClient (qwen2.5-coder:1.5b)

Оба клиента используют один жёсткий SYSTEM_PROMPT,
гарантирующий «чистую bash-команду» на выходе.
"""
from __future__ import annotations

import logging
import re
import socket
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

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


def _load_gemini_key_from_env() -> Optional[str]:
    """Ищет GEMINI_API_KEY в окружении и .env файле (рядом с CWD или домом)."""
    import os
    # 1) Уже в окружении (экспортировано в shell)
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    # 2) Пробуем загрузить из .env через python-dotenv
    try:
        from dotenv import load_dotenv  # noqa: WPS433
        for dot_env in (
            Path.cwd() / ".env",
            Path.home() / ".env",
            Path(__file__).parents[3] / ".env",  # корень проекта
        ):
            if dot_env.exists():
                load_dotenv(dot_env, override=False)
                key = os.environ.get("GEMINI_API_KEY", "").strip()
                if key:
                    return key
    except ImportError:
        pass
    return None


# ── Ollama (локальная модель) ─────────────────────────────────
class OllamaClient:
    """Запрос к локальной модели через ollama.chat()."""

    def __init__(self, model: str = OLLAMA_MODEL):
        self.model = model
        self._checked = False   # флаг однократной проверки наличия модели

    def _check_model_once(self) -> bool:
        """Проверяет, что модель загружена в Ollama (однократно при старте)."""
        if self._checked:
            return True
        self._checked = True
        try:
            import ollama  # noqa: WPS433
            models = ollama.list()
            names = []
            for m in (models.models if hasattr(models, "models") else []):
                name = m.model if hasattr(m, "model") else str(m)
                names.append(name.split(":")[0])
            model_base = self.model.split(":")[0]
            if model_base not in names:
                log.warning(
                    "Ollama: модель %r не найдена. Запусти в терминале: ollama run %s",
                    self.model, self.model,
                )
                return False
        except Exception as e:  # noqa: BLE001
            log.warning("Ollama: не удалось проверить список моделей: %s", e)
        return True

    def query(self, text: str) -> Optional[str]:
        self._check_model_once()
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
        except Exception as e:  # noqa: BLE001
            log.debug("Ollama query error: %s", e)
            return None


# ── Gemini (облачная модель) ──────────────────────────────────
class GeminiClient:
    """Запрос к Google Gemini API через google-generativeai.

    Ключ можно передать явно или оставить None — тогда загрузится
    из GEMINI_API_KEY в окружении / .env файле.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = GEMINI_MODEL):
        self._api_key = api_key or _load_gemini_key_from_env()
        if not self._api_key:
            log.error(
                "GeminiClient: GEMINI_API_KEY не найден. "
                "Добавь ключ в .env или переменную окружения. "
                "Облачный путь отключён."
            )
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
        if not self._api_key:
            return None
        try:
            client = self._get_client()
            resp = client.generate_content(
                text,
                generation_config={"temperature": 0, "max_output_tokens": 128},
            )
            return _strip_fences(resp.text) or None
        except Exception as e:  # noqa: BLE001
            log.debug("Gemini query error: %s", e)
            return None
