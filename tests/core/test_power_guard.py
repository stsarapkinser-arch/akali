"""Tests for akali.core.power_guard — exact-match power commands."""
from __future__ import annotations

import pytest

from akali.core import power_guard


@pytest.mark.parametrize("phrase, expected", [
    # poweroff
    ("выключи компьютер", "systemctl poweroff"),
    ("Выключи компьютер.", "systemctl poweroff"),
    ("  выключи компьютер  ", "systemctl poweroff"),
    ("ВЫКЛЮЧИ СИСТЕМУ", "systemctl poweroff"),
    ("выключи питание", "systemctl poweroff"),
    ("выключение системы", "systemctl poweroff"),
    ("завершение работы", "systemctl poweroff"),
    # reboot
    ("перезагрузи компьютер", "systemctl reboot"),
    ("перезагрузи систему", "systemctl reboot"),
    ("перезагрузка системы", "systemctl reboot"),
    ("перезагрузка компьютера", "systemctl reboot"),
    # suspend
    ("уйди в сон", "systemctl suspend"),
    ("режим сна", "systemctl suspend"),
    ("спящий режим", "systemctl suspend"),
    ("усыпи компьютер", "systemctl suspend"),
    # hibernate
    ("гибернация", "systemctl hibernate"),
    ("уйди в гибернацию", "systemctl hibernate"),
    ("режим гибернации", "systemctl hibernate"),
])
def test_match_exact_phrases(phrase, expected):
    assert power_guard.match(phrase) == expected


@pytest.mark.parametrize("phrase", [
    "выключи",
    "выключи комп",
    "перезагрузи",
    "сон",
    "включи свет",
    "открой браузер",
    "выключи и перезагрузи",   # combined — not exact
    "",
    None,
])
def test_match_non_phrase_returns_none(phrase):
    assert power_guard.match(phrase) is None


@pytest.mark.parametrize("cmd, expected", [
    ("systemctl poweroff", True),
    ("systemctl reboot", True),
    ("systemctl suspend", True),
    ("systemctl hibernate", True),
    ("poweroff", True),
    ("reboot", True),
    ("shutdown -h now", True),
    ("halt", True),
    ("init 0", True),
    ("init 6", True),
    ("SYSTEMCTL POWEROFF", True),
    ("  systemctl reboot  ", True),
    ("ls", False),
    ("firefox &", False),
    ("rm file.txt", False),
    ("", False),
    ("echo poweroff", False),  # not at start
])
def test_is_power_command(cmd, expected):
    assert power_guard.is_power_command(cmd) is expected


def test_all_phrases_returns_list_of_tuples():
    phrases = power_guard.all_phrases()
    assert isinstance(phrases, list)
    assert len(phrases) >= 16
    for item in phrases:
        assert isinstance(item, tuple)
        assert len(item) == 2
        phrase, bash = item
        assert isinstance(phrase, str)
        assert bash.startswith("systemctl ")


def test_all_phrases_no_duplicates():
    phrases = power_guard.all_phrases()
    just_phrases = [p for p, _ in phrases]
    assert len(just_phrases) == len(set(just_phrases))


def test_punctuation_stripped():
    assert power_guard.match("выключи, компьютер!") == "systemctl poweroff"
    assert power_guard.match("режим... сна?") == "systemctl suspend"
