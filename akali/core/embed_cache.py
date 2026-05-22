"""FastEmbed-эмбеддинги с дисковым кэшем матрицы базы команд.

Матрица сохраняется в  <commands_file>.embedcache.pkl  и пересчитывается
только если mtime файла базы изменился или модель сменилась.

fastembed работает только на CPU, без CUDA — идеально для Intel N100.
"""
from __future__ import annotations

import logging
import os
import pickle
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# paraphrase-multilingual-MiniLM-L12-v2 поддерживается всеми версиями fastembed
DEFAULT_EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_CACHE_SUFFIX = ".embedcache.pkl"
_FALLBACK_KEYWORD = "multilingual"


def _find_multilingual_model(TextEmbedding) -> str | None:
    """Ищет поддерживаемую multilingual-модель в списке fastembed."""
    try:
        supported = TextEmbedding.list_supported_models()
        for entry in supported:
            name = entry.get("model", "") if isinstance(entry, dict) else str(entry)
            if _FALLBACK_KEYWORD in name.lower():
                return name
        # Нет multilingual — берём первую попавшуюся
        if supported:
            first = supported[0]
            return first.get("model", "") if isinstance(first, dict) else str(first)
    except Exception:
        pass
    return None


class EmbedCache:
    """Обёртка вокруг fastembed.TextEmbedding с кэшированием базы команд."""

    def __init__(self, model_name: str = DEFAULT_EMBED_MODEL):
        self.model_name = model_name
        self._model = None   # lazy-init: не грузим при импорте

    # ── embed ──────────────────────────────────────────────────
    def _get_model(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding  # noqa: WPS433
                try:
                    self._model = TextEmbedding(model_name=self.model_name)
                except Exception as e:
                    # Модель не поддерживается — ищем любую multilingual-замену
                    log.warning("FastEmbed: модель %r недоступна (%s), ищу замену…",
                                self.model_name, e)
                    fallback = _find_multilingual_model(TextEmbedding)
                    if not fallback:
                        raise RuntimeError(
                            f"FastEmbed: модель {self.model_name!r} не поддерживается "
                            f"и замены не найдено. Исходная ошибка: {e}"
                        ) from e
                    log.warning("FastEmbed: использую замену %r", fallback)
                    self.model_name = fallback
                    self._model = TextEmbedding(model_name=fallback)
            except ImportError as e:
                raise RuntimeError(
                    f"fastembed не установлен. Установи: pip install fastembed. {e}"
                ) from e
        return self._model

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Эмбеддинг пачки текстов → матрица (N, D) float32."""
        model = self._get_model()
        return np.array(list(model.embed(texts)), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """Эмбеддинг одного запроса → вектор (D,) float32."""
        return self.embed_batch([text])[0]

    # ── DB cache ───────────────────────────────────────────────
    def load_db(
        self,
        commands_file: Path,
        pairs: list[tuple[str, str]],  # [(trigger, bash_cmd), ...]
    ) -> tuple[np.ndarray, list[str]]:
        """Загружает эмбеддинги базы из кэша или пересчитывает.

        Returns:
            embs:  матрица (N, D) float32
            cmds:  list[str] len N — bash-команды для каждого триггера
        """
        if not pairs:
            return np.zeros((0, 1), dtype=np.float32), []

        cache_path = commands_file.with_name(commands_file.name + _CACHE_SUFFIX)
        cmd_mtime = commands_file.stat().st_mtime if commands_file.exists() else 0.0

        # Пробуем загрузить кэш
        if cache_path.exists():
            try:
                with cache_path.open("rb") as f:
                    saved = pickle.load(f)
                if (
                    saved.get("mtime") == cmd_mtime
                    and saved.get("model") == self.model_name
                    and len(saved.get("cmds", [])) == len(pairs)
                ):
                    return saved["embs"], saved["cmds"]
            except (pickle.UnpicklingError, KeyError, TypeError, OSError):
                pass

        # Пересчитываем
        triggers = [t for t, _ in pairs]
        cmds = [c for _, c in pairs]
        embs = self.embed_batch(triggers)

        # Атомарно сохраняем кэш
        try:
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps({
                "mtime": cmd_mtime,
                "model": self.model_name,
                "embs": embs,
                "cmds": cmds,
            }))
            os.replace(tmp, cache_path)
        except OSError:
            pass

        return embs, cmds

    # ── similarity ─────────────────────────────────────────────
    @staticmethod
    def cosine_top1(
        query_vec: np.ndarray,
        db_embs: np.ndarray,
        db_cmds: list[str],
        threshold: float,
    ) -> tuple[str | None, float]:
        """Батчевое косинусное сравнение запроса с базой.

        Returns:
            (best_command, best_score) — команда или None если ниже порога.
        """
        if db_embs.shape[0] == 0:
            return None, 0.0
        q_norm = float(np.linalg.norm(query_vec))
        if q_norm < 1e-9:
            return None, 0.0
        norms = np.linalg.norm(db_embs, axis=1)
        denom = norms * q_norm
        denom = np.where(denom < 1e-9, 1e-9, denom)
        scores = (db_embs @ query_vec) / denom
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        cmd = db_cmds[best_idx] if best_score >= threshold else None
        return cmd, best_score
