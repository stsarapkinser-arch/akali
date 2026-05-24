"""Страница «Настройки» — минимум, всё нужное, без мусора.

Карточки:
    • Аудио — выбор микрофона.
    • Активация — wake-words, реиндекс-фразы.
    • Облачный LLM — Gemini API key + кнопка проверки.
    • TTS — голосовой ответ ассистента (включить/выключить, движок).
    • Репозиторий и обновление — путь, проверка, обновить, авто-проверка.
    • Самопроверка — запустить system_check.

Старые слайдеры fuzzy/vector убраны: пайплайн теперь единый
(power_guard → fastembed >0.85 → LLM), пороги задаются константами.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QSettings, Qt, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame,
                                QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                                QProgressBar, QPushButton, QScrollArea, QSpinBox,
                                QTextEdit, QVBoxLayout, QWidget)

from ...core.backend import AssistantCore
from ...core.audio_worker import list_input_devices
from ...core.updater import UpdateResult, VersionInfo

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# Карточка обновления — оставлена почти как была, причёсана.
# ────────────────────────────────────────────────────────────
class UpdatePanel(QFrame):
    check_requested = Signal()
    update_requested = Signal(bool)   # force

    def __init__(self, repo_dir: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._repo_dir = repo_dir
        self._busy = False
        self.setObjectName("updatePanel")
        self.setFrameShape(QFrame.NoFrame)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── SHA + branch ──
        ver_row = QHBoxLayout()
        ver_row.setSpacing(10)
        self._lbl_sha = QLabel("SHA: —")
        self._lbl_sha.setStyleSheet("color: #8B949E; font-size: 10px;")
        ver_row.addWidget(self._lbl_sha)
        self._lbl_branch = QLabel("⎇  —")
        self._lbl_branch.setStyleSheet(
            "color: #00D4FF; font-size: 10px; font-weight: bold;")
        ver_row.addWidget(self._lbl_branch)
        ver_row.addStretch()
        layout.addLayout(ver_row)

        # ── Статус ──
        self._lbl_status = QLabel("Нажми «Проверить» для поиска обновлений")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet(
            "color: #8B949E; font-size: 10px; padding: 6px; "
            "background-color: #161B22; border-radius: 4px;")
        layout.addWidget(self._lbl_status)

        # ── Changelog ──
        self._changelog = QTextEdit()
        self._changelog.setObjectName("changelog")
        self._changelog.setReadOnly(True)
        self._changelog.setVisible(False)
        self._changelog.setMaximumHeight(130)
        layout.addWidget(self._changelog)

        # ── Прогресс ──
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setMaximumHeight(6)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        # ── Кнопки ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_check = QPushButton("🔍  Проверить")
        self._btn_check.setObjectName("secondaryBtn")
        self._btn_check.setMinimumHeight(34)
        self._btn_check.setCursor(Qt.PointingHandCursor)
        self._btn_check.clicked.connect(self._on_check_clicked)
        btn_row.addWidget(self._btn_check)

        self._btn_update = QPushButton("⬇  Обновить")
        self._btn_update.setObjectName("primaryBtn")
        self._btn_update.setMinimumHeight(34)
        self._btn_update.setCursor(Qt.PointingHandCursor)
        self._btn_update.setEnabled(False)
        self._btn_update.setVisible(False)
        self._btn_update.clicked.connect(self._on_update_clicked)
        btn_row.addWidget(self._btn_update)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Force-чекбокс (stash + reset --hard) ──
        self._chk_force = QCheckBox(
            "Принудительно: stash локальные, при конфликте reset --hard на remote.")
        self._chk_force.setStyleSheet("font-size: 10px; color: #8B949E;")
        layout.addWidget(self._chk_force)

    @property
    def force(self) -> bool:
        return self._chk_force.isChecked()

    def _set_status(self, text: str, color: str = "#8B949E") -> None:
        """Обновляет статусную метку с цветом. Раньше это был метод,
        потом исчез при рефакторинге — кнопка «Проверить» падала с
        AttributeError. Восстанавливаем."""
        self._lbl_status.setText(text)
        self._lbl_status.setStyleSheet(
            f"color: {color}; font-size: 10px; padding: 6px; "
            "background-color: #161B22; border-radius: 4px;")

    @Slot(object)
    def on_version_info(self, info: VersionInfo) -> None:
        self._set_busy(False)
        if info.short_sha:
            self._lbl_sha.setText(f"SHA: {info.short_sha}")
        if info.branch:
            self._lbl_branch.setText(f"⎇  {info.branch}")
        if info.error:
            self._set_status(f"⚠ Ошибка: {info.error}", "#F85149")
            return
        if not info.remote_reachable:
            self._set_status("⚠ Нет доступа к remote. Проверь сеть.", "#D29922")
            return
        if not info.has_updates:
            self._set_status("✓ Версия актуальна", "#3FB950")
            self._btn_update.setVisible(False)
            self._changelog.setVisible(False)
            return
        count = info.commits_behind
        self._set_status(f"⬇ Доступно {count} новых коммитов", "#D29922")
        lines = [f"  {sha}  {msg}" for sha, msg in info.remote_commits]
        self._changelog.setPlainText("\n".join(lines))
        self._changelog.setVisible(True)
        self._btn_update.setEnabled(True)
        self._btn_update.setVisible(True)

    @Slot(object)
    def on_update_result(self, result: UpdateResult) -> None:
        self._set_busy(False)
        self._btn_update.setVisible(False)
        self._changelog.setVisible(False)
        if not result.ok:
            self._set_status(f"✕ Ошибка обновления: {result.error}", "#F85149")
            return
        if result.already_up_to_date:
            self._set_status("✓ Уже последняя версия", "#3FB950")
            return
        count = len(result.pulled_commits)
        lines = [f"  {sha}  {msg}" for sha, msg in result.pulled_commits]
        self._changelog.setPlainText("\n".join(lines))
        self._changelog.setVisible(True)
        extra = []
        if result.stashed:
            extra.append("стэш сохранён")
        if result.forced_reset:
            extra.append("reset --hard")
        suffix = (" · " + ", ".join(extra)) if extra else ""
        restart_note = "  ⚠ Перезапуск" if result.needs_restart else ""
        self._set_status(
            f"✓ Обновлено: +{count} коммитов{suffix}{restart_note}",
            "#D29922" if result.needs_restart else "#3FB950")

    def _on_check_clicked(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_status("Проверка обновлений…", "#8B949E")
        self._changelog.setVisible(False)
        self._btn_update.setVisible(False)
        self.check_requested.emit()

    def _on_update_clicked(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_status("Загрузка обновлений…", "#00D4FF")
        self.update_requested.emit(self.force)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._progress.setVisible(busy)
        self._btn_check.setEnabled(not busy)
        self._btn_update.setEnabled(not busy and self._btn_update.isVisible())
        self._btn_check.setText("⏳  Проверка…" if busy else "🔍  Проверить")


# ────────────────────────────────────────────────────────────
def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("sectionLabel")
    return lbl


class _Card(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.setFrameShape(QFrame.NoFrame)
        self._inner = QVBoxLayout(self)
        self._inner.setContentsMargins(12, 10, 12, 12)
        self._inner.setSpacing(6)

    def add(self, w):
        if isinstance(w, QWidget):
            self._inner.addWidget(w)
        else:
            self._inner.addLayout(w)


def _form_row(label: str, widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    lbl = QLabel(label)
    lbl.setObjectName("formLabel")
    lbl.setMinimumWidth(110)
    row.addWidget(lbl, 0)
    row.addWidget(widget, 1)
    return row


# ────────────────────────────────────────────────────────────
class SettingsPage(QWidget):
    """Все настройки в одной скроллируемой колонке."""

    update_requested = Signal(bool)        # force
    check_update_requested = Signal()
    reload_requested = Signal()
    device_changed = Signal(object)        # int|None
    auto_update_changed = Signal(int)      # минуты (0 = выкл.)
    tts_settings_changed = Signal(bool, str, str)   # enabled, engine, piper_voice
    gemini_test_requested = Signal(str)    # API key для теста
    llm_mode_changed = Signal(str)          # auto | gemini-only | ollama-only
    system_check_requested = Signal()

    def __init__(self, core: AssistantCore, settings: QSettings,
                 repo_dir: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir
        self._build()
        self._populate_devices()
        self._load_into_widgets()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        title = QLabel("Настройки")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        # ── Аудио ───────────────────────────────────────
        audio_card = _Card()
        audio_card.add(_section_label("АУДИО"))
        self._cmb_device = QComboBox()
        self._cmb_device.setMinimumHeight(30)
        audio_card.add(_form_row("Микрофон", self._cmb_device))
        col.addWidget(audio_card)

        # ── Активация ───────────────────────────────────
        words_card = _Card()
        words_card.add(_section_label("АКТИВАЦИЯ"))
        self._edit_wake = QLineEdit()
        self._edit_wake.setPlaceholderText("акали, ассистент, компьютер")
        self._edit_reindex = QLineEdit()
        self._edit_reindex.setPlaceholderText("переиндексируй, обнови команды")
        words_card.add(_form_row("Слова",   self._edit_wake))
        words_card.add(_form_row("Реиндекс", self._edit_reindex))
        col.addWidget(words_card)

        # ── Облачный LLM ────────────────────────────────
        llm_card = _Card()
        llm_card.add(_section_label("ОБЛАЧНЫЙ LLM (GEMINI)"))
        self._edit_gemini_key = QLineEdit()
        self._edit_gemini_key.setPlaceholderText("AIza… — необязательно, ключ для gemini-2.5-flash")
        self._edit_gemini_key.setEchoMode(QLineEdit.Password)
        llm_card.add(_form_row("API key", self._edit_gemini_key))
        gemini_btn = QPushButton("Проверить ключ Gemini")
        gemini_btn.setObjectName("secondaryBtn")
        gemini_btn.setMinimumHeight(28)
        gemini_btn.clicked.connect(self._on_gemini_test_clicked)
        llm_card.add(gemini_btn)

        # Селектор режима маршрутизации LLM
        self._cmb_llm_mode = QComboBox()
        self._cmb_llm_mode.addItem(
            "auto — сначала Gemini, при ошибке фоллбэк на локальную модель", "auto")
        self._cmb_llm_mode.addItem(
            "gemini-only — только облачная (без локального фоллбэка)", "gemini-only")
        self._cmb_llm_mode.addItem(
            "ollama-only — только локальная (полностью оффлайн)", "ollama-only")
        self._cmb_llm_mode.setMinimumHeight(28)
        llm_card.add(_form_row("Режим", self._cmb_llm_mode))
        col.addWidget(llm_card)

        # ── TTS ─────────────────────────────────────────
        tts_card = _Card()
        tts_card.add(_section_label("ГОЛОСОВОЙ ОТВЕТ"))
        self._chk_tts_enabled = QCheckBox("Включить голосовой ответ")
        tts_card.add(self._chk_tts_enabled)
        self._cmb_tts_voice = QComboBox()
        self._cmb_tts_voice.addItem("auto (xtts если готов, иначе piper, иначе espeak)", "auto")
        self._cmb_tts_voice.addItem("xtts (клонированный голос Джарвиса)", "xtts")
        self._cmb_tts_voice.addItem("piper (нейронный, мужской/женский ru)", "piper")
        self._cmb_tts_voice.addItem("espeak-ng (резерв)", "espeak")
        self._cmb_tts_voice.addItem("выключить", "off")
        self._cmb_tts_voice.setMinimumHeight(28)
        tts_card.add(_form_row("Движок", self._cmb_tts_voice))

        # Селектор голоса Piper. dmitri = мужской в духе джарвиса.
        self._cmb_piper_voice = QComboBox()
        self._cmb_piper_voice.addItem("Дмитрий (мужской, спокойный — Джарвис)",
                                       "ru_RU-dmitri-medium")
        self._cmb_piper_voice.addItem("Руслан (мужской, низкий)",
                                       "ru_RU-ruslan-medium")
        self._cmb_piper_voice.addItem("Денис (мужской, нейтральный)",
                                       "ru_RU-denis-medium")
        self._cmb_piper_voice.addItem("Ирина (женский)",
                                       "ru_RU-irina-medium")
        self._cmb_piper_voice.setMinimumHeight(28)
        tts_card.add(_form_row("Голос Piper", self._cmb_piper_voice))
        col.addWidget(tts_card)

        # ── Репозиторий + обновление ────────────────────
        repo_card = _Card()
        repo_card.add(_section_label("РЕПОЗИТОРИЙ И ОБНОВЛЕНИЕ"))
        self._edit_repo = QLineEdit(self._repo_dir)
        repo_row = QHBoxLayout()
        repo_row.setContentsMargins(0, 0, 0, 0)
        repo_row.setSpacing(6)
        repo_row.addWidget(self._edit_repo, 1)
        browse = QPushButton("…")
        browse.setObjectName("secondaryBtn")
        browse.setFixedWidth(36)
        browse.clicked.connect(self._browse_repo)
        repo_row.addWidget(browse)
        wrap = QWidget()
        wrap.setLayout(repo_row)
        repo_card.add(_form_row("Папка", wrap))

        # Авто-проверка обновлений
        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(0, 0, 0, 0)
        auto_row.setSpacing(6)
        self._spin_auto_update = QSpinBox()
        self._spin_auto_update.setRange(0, 1440)
        self._spin_auto_update.setSuffix(" мин")
        self._spin_auto_update.setMinimumHeight(28)
        self._spin_auto_update.setToolTip(
            "Как часто проверять GitHub на обновления. 0 = выключено.")
        auto_row.addWidget(self._spin_auto_update, 1)
        auto_wrap = QWidget()
        auto_wrap.setLayout(auto_row)
        repo_card.add(_form_row("Авто-проверка", auto_wrap))

        self._update_panel = UpdatePanel(self._repo_dir)
        self._update_panel.check_requested.connect(self.check_update_requested.emit)
        self._update_panel.update_requested.connect(self.update_requested.emit)
        repo_card.add(self._update_panel)
        col.addWidget(repo_card)

        # ── Самопроверка компонентов ────────────────────
        check_card = _Card()
        check_card.add(_section_label("САМОПРОВЕРКА"))
        check_card.add(QLabel("Проверить Ollama, qwen2.5-coder:1.5b, piper, Vosk."))
        self._btn_check = QPushButton("🩺  Проверить компоненты")
        self._btn_check.setObjectName("secondaryBtn")
        self._btn_check.setMinimumHeight(30)
        self._btn_check.clicked.connect(self.system_check_requested.emit)
        check_card.add(self._btn_check)
        col.addWidget(check_card)

        # ── Применить ───────────────────────────────────
        apply_card = _Card()
        self._btn_apply = QPushButton("Применить и перезагрузить базу")
        self._btn_apply.setObjectName("primaryBtn")
        self._btn_apply.setCursor(Qt.PointingHandCursor)
        self._btn_apply.setMinimumHeight(36)
        self._btn_apply.clicked.connect(self._apply)
        apply_card.add(self._btn_apply)
        col.addWidget(apply_card)

        col.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

    # ── Заполнение и сохранение ──────────────────────────
    def _populate_devices(self) -> None:
        self._cmb_device.clear()
        self._cmb_device.addItem("Авто (PortAudio default)", None)
        for idx, name, ch in list_input_devices():
            label = f"#{idx} · {name}  [{ch}ch]"
            if len(label) > 56:
                label = label[:55] + "…"
            self._cmb_device.addItem(label, idx)
        saved = self._settings.value("audio_device", None)
        if saved not in (None, ""):
            try:
                want = int(saved)
                for i in range(self._cmb_device.count()):
                    if self._cmb_device.itemData(i) == want:
                        self._cmb_device.setCurrentIndex(i)
                        break
            except (TypeError, ValueError):
                self._cmb_device.setCurrentIndex(0)
        self._cmb_device.currentIndexChanged.connect(self._on_device_changed)

    @Slot(int)
    def _on_device_changed(self, _idx: int) -> None:
        dev = self._cmb_device.currentData()
        self._settings.setValue("audio_device",
                                "" if dev is None else int(dev))
        self.device_changed.emit(dev)

    def _browse_repo(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Папка проекта", self._repo_dir)
        if path:
            self._edit_repo.setText(path)

    def _load_into_widgets(self) -> None:
        s = self._settings
        self._edit_wake.setText(", ".join(self._core.wake_words))
        self._edit_reindex.setText(", ".join(self._core.reindex_triggers))
        self._edit_gemini_key.setText(str(s.value("gemini_api_key", "") or ""))
        # LLM mode
        llm_mode = str(s.value("llm_mode", "auto") or "auto")
        idx = self._cmb_llm_mode.findData(llm_mode)
        if idx >= 0:
            self._cmb_llm_mode.setCurrentIndex(idx)
        # TTS
        tts_on = str(s.value("tts_enabled", "true")).lower() in ("1", "true", "yes")
        self._chk_tts_enabled.setChecked(tts_on)
        voice = str(s.value("tts_voice", "auto") or "auto")
        idx = self._cmb_tts_voice.findData(voice)
        if idx >= 0:
            self._cmb_tts_voice.setCurrentIndex(idx)
        piper_voice = str(s.value(
            "tts_piper_voice", "ru_RU-dmitri-medium",
        ) or "ru_RU-dmitri-medium")
        idx = self._cmb_piper_voice.findData(piper_voice)
        if idx >= 0:
            self._cmb_piper_voice.setCurrentIndex(idx)
        # Auto-update
        try:
            self._spin_auto_update.setValue(int(s.value("auto_update_minutes", 0) or 0))
        except (TypeError, ValueError):
            self._spin_auto_update.setValue(0)

    def _apply(self) -> None:
        wake = [w.strip().lower() for w in self._edit_wake.text().split(",") if w.strip()]
        reindex = [r.strip().lower() for r in self._edit_reindex.text().split(",") if r.strip()]
        if wake:
            self._core.wake_words = wake
        if reindex:
            self._core.reindex_triggers = reindex

        s = self._settings
        s.setValue("wake_words", ",".join(self._core.wake_words))
        s.setValue("reindex_triggers", ",".join(self._core.reindex_triggers))
        api_key = self._edit_gemini_key.text().strip()
        s.setValue("gemini_api_key", api_key)
        llm_mode = self._cmb_llm_mode.currentData() or "auto"
        s.setValue("llm_mode", llm_mode)
        s.setValue("repo_dir", self._edit_repo.text().strip() or self._repo_dir)
        self._repo_dir = self._edit_repo.text().strip() or self._repo_dir

        # TTS
        tts_on = self._chk_tts_enabled.isChecked()
        voice = self._cmb_tts_voice.currentData() or "auto"
        piper_voice = (self._cmb_piper_voice.currentData()
                        or "ru_RU-dmitri-medium")
        s.setValue("tts_enabled", "true" if tts_on else "false")
        s.setValue("tts_voice", voice)
        s.setValue("tts_piper_voice", piper_voice)
        self.tts_settings_changed.emit(tts_on, voice, piper_voice)

        # Auto-update interval
        minutes = int(self._spin_auto_update.value())
        s.setValue("auto_update_minutes", minutes)
        self.auto_update_changed.emit(minutes)

        # Обновляем ключ Gemini в роутере без полной перезагрузки
        self._core.update_gemini_key(api_key or None)
        self._core.update_llm_mode(llm_mode)
        self.llm_mode_changed.emit(llm_mode)

        self.reload_requested.emit()

    # ── Сигналы от UpdatePanel ───────────────────────────
    @Slot(object)
    def on_version_info(self, info: VersionInfo) -> None:
        self._update_panel.on_version_info(info)

    @Slot(object)
    def on_update_result(self, result: UpdateResult) -> None:
        self._update_panel.on_update_result(result)

    def show_check_result(self, lines: list[str]) -> None:
        """Координатор передаёт сюда результат system_check."""
        QMessageBox.information(self, "Самопроверка компонентов", "\n".join(lines))

    def _on_gemini_test_clicked(self) -> None:
        api_key = self._edit_gemini_key.text().strip()
        if not api_key:
            QMessageBox.warning(self, "Нет ключа", "Сначала введи API key.")
            return
        self.gemini_test_requested.emit(api_key)

    def show_gemini_test_result(self, ok: bool, message: str) -> None:
        title = "Gemini OK" if ok else "Gemini ошибка"
        if ok:
            QMessageBox.information(self, title, message)
        else:
            QMessageBox.warning(self, title, message)

    @property
    def repo_dir(self) -> str:
        return self._edit_repo.text().strip() or self._repo_dir

    @property
    def current_device(self):
        return self._cmb_device.currentData()
