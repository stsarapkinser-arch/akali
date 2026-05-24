"""Tests for akali.core.matcher — wake-word and reindex detection."""
from __future__ import annotations

import pytest

from akali.core import matcher


WAKE = ("акали", "ассистент", "компьютер")


@pytest.mark.parametrize("words, expected_idx", [
    (["акали", "включи", "свет"], 0),
    (["ассистент"], 0),
    (["компьютер", "что", "там"], 0),
    (["привет", "ассистент"], 1),
    (["включи", "свет"], -1),
    ([], -1),
])
def test_detect_wake_word(words, expected_idx):
    idx = matcher.detect_wake_word(words, WAKE, 0.75)
    assert idx == expected_idx


def test_detect_wake_word_with_typo_above_threshold():
    # "акалі" (одна буква отличается) — должен сматчиться при умеренном пороге
    idx = matcher.detect_wake_word(["акалі"], WAKE, 0.7)
    assert idx == 0


def test_detect_wake_word_typo_below_threshold():
    # Очень высокий порог не пропускает опечатку
    idx = matcher.detect_wake_word(["акалі"], WAKE, 0.99)
    assert idx == -1


def test_detect_wake_word_empty_wake_list():
    assert matcher.detect_wake_word(["акали"], [], 0.5) == -1


def test_detect_wake_word_returns_first_match():
    # При нескольких словах возвращает индекс первого совпавшего
    idx = matcher.detect_wake_word(["акали", "ассистент"], WAKE, 0.75)
    assert idx == 0


REINDEX_TRIGGERS = ("переиндексируй", "обнови команд", "пересканируй систем")


@pytest.mark.parametrize("text, expected", [
    ("переиндексируй", True),
    ("Переиндексируй", True),
    ("обнови команды", True),
    ("обнови команд срочно", True),
    ("акали, переиндексируй сейчас", True),
    ("пересканируй систему пожалуйста", True),
    ("открой браузер", False),
    ("обнови меня", False),  # нет "команд"
    ("", False),
])
def test_is_reindex_phrase(text, expected):
    assert matcher.is_reindex_phrase(text, REINDEX_TRIGGERS) is expected


def test_is_reindex_phrase_empty_triggers():
    assert matcher.is_reindex_phrase("переиндексируй", []) is False
