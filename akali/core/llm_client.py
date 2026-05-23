"""LLM-клиенты и проверка сети для Query Router.

Иерархия:
    check_internet()  →  True  →  GeminiClient (gemini-2.5-flash)
                      →  False →  OllamaClient (qwen2.5-coder:1.5b)

Оба клиента используют один жёсткий SYSTEM_PROMPT, гарантирующий
«чистую bash-команду» на выходе.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Константы ────────────────────────────────────────────────
NETWORK_HOST = "8.8.8.8"
NETWORK_PORT = 53
NETWORK_TIMEOUT = 1.5          # сек; жёсткий лимит на N100
NETWORK_CACHE_TTL = 30.0       # сек; кэш положительного результата

OLLAMA_MODEL = "qwen2.5-coder:1.5b"
GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = (
    "Ты — системный модуль трансляции текста в команды для Kali Linux 2026.2 "
    "(Wayland, KDE Plasma 6). Железо: Intel N100.\n"
    "ПРАВИЛА:\n"
    "1. Выдавай ТОЛЬКО чистую bash-команду.\n"
    "2. Никакого Markdown, никаких ```bash, никаких пояснений.\n"
    "3. Если команда открывает графическое приложение, ОБЯЗАТЕЛЬНО ставь в конце &.\n"
    "4. Для управления окнами используй только qdbus (xdotool не работает на Wayland).\n"
    "5. Никаких символов-разделителей: ; | && || > <\n"
    "6. НИКОГДА не выдавай команды выключения/перезагрузки/гибернации/удаления "
    "(poweroff, reboot, shutdown, halt, rm -rf, mkfs, dd of=/dev) — это сделает другой модуль.\n"
    "Если не знаешь точную команду, выведи: echo error"
)

_MD_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.MULTILINE)


# ── Кэш для check_internet ───────────────────────────────────
_net_lock = threading.Lock()
_net_state: tuple[float, bool] = (0.0, False)  # (timestamp, value)


def check_internet() -> bool:
    """Быстрая проверка сети через TCP к 8.8.8.8:53.

    Положительный результат кэшируется на NETWORK_CACHE_TTL секунд,
    чтобы не дёргать сеть на каждый запрос. Отрицательный — не
    кэшируем, чтобы быстро вернуться к Gemini при восстановлении сети.
    """
    global _net_state
    now = time.monotonic()
    with _net_lock:
        ts, val = _net_state
        if val and (now - ts) < NETWORK_CACHE_TTL:
            return True
    try:
        with socket.create_connection((NETWORK_HOST, NETWORK_PORT),
                                      timeout=NETWORK_TIMEOUT):
            with _net_lock:
                _net_state = (now, True)
            return True
    except (OSError, socket.timeout):
        with _net_lock:
            _net_state = (now, False)
        return False


def _strip_fences(raw: str) -> str:
    """Удаляет markdown-ограждения ```bash ... ``` и берёт первую строку."""
    cleaned = _MD_FENCE.sub("", raw).strip()
    first_line = cleaned.splitlines()[0].strip() if cleaned else ""
    return first_line


def _load_gemini_key_from_env() -> Optional[str]:
    """Ищет GEMINI_API_KEY в окружении и .env файле."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    try:
        from dotenv import load_dotenv  # noqa: WPS433
        for dot_env in (
            Path.cwd() / ".env",
            Path.home() / ".env",
            Path(__file__).parents[3] / ".env",
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
        self._checked = False

    def _check_model_once(self) -> bool:
        if self._checked:
            return True
        self._checked = True
        try:
            import ollama  # noqa: WPS433
            models = ollama.list()
            names: list[str] = []
            for m in (models.models if hasattr(models, "models") else []):
                name = m.model if hasattr(m, "model") else str(m)
                names.append(name.split(":")[0])
            model_base = self.model.split(":")[0]
            if model_base not in names:
                log.warning(
                    "Ollama: модель %r не загружена. Запусти: ollama pull %s",
                    self.model, self.model,
                )
                return False
        except Exception as e:  # noqa: BLE001
            log.debug("Ollama: проверка моделей не удалась: %s", e)
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

    Ключ можно передать явно или оставить None — тогда загрузится из
    GEMINI_API_KEY в окружении / .env.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = GEMINI_MODEL):
        self._api_key = (api_key or _load_gemini_key_from_env() or "").strip() or None
        self.model = model
        self._client = None
        if not self._api_key:
            log.info("GeminiClient: API ключ не задан — облачный путь выключен")

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

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
                    "google-generativeai не установлен. "
                    "pip install google-generativeai"
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
            return _strip_fences(getattr(resp, "text", "") or "") or None
        except Exception as e:  # noqa: BLE001
            log.debug("Gemini query error: %s", e)
            return None
