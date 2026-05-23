"""Страница «Настройки»: компактные секции в виде карточек.

Содержание:
    • Карточка «Аудио»  — выбор микрофона.
    • Карточка «Распознавание»  — три порога.
    • Карточка «Слова»  — wake-words и реиндекс-фразы.
    • Карточка «Репозиторий»  — путь + git pull.
    • Карточка «Действия»  — применить, реиндекс.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                                QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                                QPushButton, QScrollArea, QSizePolicy,
                                QSlider, QTextEdit, QVBoxLayout, QWidget)

from ...core.backend import AssistantCore
from ...core.audio_worker import list_input_devices
from ...core.updater import UpdateResult, VersionInfo


class UpdatePanel(QFrame):
    """Полноценная панель обновления прямо в настройках.

    Отображает:
      • Текущий SHA + ветку
      • Статус (актуально / N новых коммитов)
      • Список доступных коммитов (changelog)
      • Прогресс-бар во время операции
      • Лог результата
    """

    check_requested = Signal()
    update_requested = Signal()

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

        # ── Текущая версия ──────────────────────────────────────
        ver_row = QHBoxLayout()
        ver_row.setSpacing(10)

        self._lbl_sha = QLabel("SHA: —")
        self._lbl_sha.setStyleSheet("color: #8B949E; font-size: 10px;")
        ver_row.addWidget(self._lbl_sha)

        self._lbl_branch = QLabel("branch: —")
        self._lbl_branch.setStyleSheet(
            "color: #00D4FF; font-size: 10px; font-weight: bold;")
        ver_row.addWidget(self._lbl_branch)
        ver_row.addStretch()
        layout.addLayout(ver_row)

        # ── Статус обновлений ───────────────────────────────────
        self._lbl_status = QLabel("Нажми «Проверить» для поиска обновлений")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet(
            "color: #8B949E; font-size: 10px; padding: 6px; "
            "background-color: #161B22; border-radius: 4px;")
        layout.addWidget(self._lbl_status)

        # ── Changelog (новые коммиты) ───────────────────────────
        self._changelog = QTextEdit()
        self._changelog.setObjectName("changelog")
        self._changelog.setReadOnly(True)
        self._changelog.setVisible(False)
        self._changelog.setMaximumHeight(130)
        self._changelog.setStyleSheet("""
            QTextEdit#changelog {
                background-color: #0D1117;
                color: #3FB950;
                font-size: 9px;
                font-family: "JetBrains Mono", monospace;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px;
            }
        """)
        layout.addWidget(self._changelog)

        # ── Прогресс-бар ────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setObjectName("updateProgress")
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setVisible(False)
        self._progress.setMaximumHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar#updateProgress {
                background-color: #30363D;
                border: none;
                border-radius: 3px;
            }
            QProgressBar#updateProgress::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00D4FF, stop:1 #1D8EE6);
                border-radius: 3px;
            }
        """)
        layout.addWidget(self._progress)

        # ── Кнопки ─────────────────────────────────────────────
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

        self.setLayout(layout)

    # ── Публичные слоты ─────────────────────────────────────────
    @Slot(object)
    def on_version_info(self, info: VersionInfo) -> None:
        """Вызывается после проверки обновлений."""
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

        # Есть обновления — показываем changelog
        count = info.commits_behind
        self._set_status(
            f"⬇ Доступно {count} {'коммит' if count == 1 else 'коммита' if count < 5 else 'коммитов'}",
            "#D29922")

        lines = [f"  {sha}  {msg}" for sha, msg in info.remote_commits]
        self._changelog.setPlainText("\n".join(lines))
        self._changelog.setVisible(True)

        self._btn_update.setEnabled(True)
        self._btn_update.setVisible(True)

    @Slot(object)
    def on_update_result(self, result: UpdateResult) -> None:
        """Вызывается после выполнения git pull."""
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

        restart_note = "  ⚠ Перезапусти приложение" if result.needs_restart else ""
        self._set_status(
            f"✓ Обновлено: +{count} коммитов{restart_note}",
            "#3FB950" if not result.needs_restart else "#D29922")

    def set_repo_dir(self, path: str) -> None:
        self._repo_dir = path

    # ── Приватные методы ────────────────────────────────────────
    def _on_check_clicked(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_status("Проверка обновлений...", "#8B949E")
        self._changelog.setVisible(False)
        self._btn_update.setVisible(False)
        self.check_requested.emit()

    def _on_update_clicked(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_status("Загрузка обновлений...", "#00D4FF")
        self.update_requested.emit()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._progress.setVisible(busy)
        self._btn_check.setEnabled(not busy)
        self._btn_update.setEnabled(not busy)
        self._btn_check.setText("⏳  Проверка..." if busy else "🔍  Проверить")

    def _set_status(self, text: str, color: str) -> None:
        self._lbl_status.setText(text)
        self._lbl_status.setStyleSheet(
            f"color: {color}; font-size: 10px; padding: 6px; "
            "background-color: #161B22; border-radius: 4px; font-weight: bold;")


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("sectionLabel")
    return lbl


class _Card(QFrame):
    """Простая карточка-контейнер с тонкой границей."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.setFrameShape(QFrame.NoFrame)
        self._inner = QVBoxLayout(self)
        self._inner.setContentsMargins(12, 10, 12, 12)
        self._inner.setSpacing(6)

    def add(self, w):  # noqa: D401
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


class SettingsPage(QWidget):
    """Все настройки в одной скроллируемой колонке."""

    update_requested = Signal()
    check_update_requested = Signal()
    reload_requested = Signal()
    device_changed = Signal(object)  # int|None — индекс устройства

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
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        host.setObjectName("settingsHost")
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)

        # === Аудио ============================================
        audio_card = _Card()
        audio_card.add(_section_label("АУДИО"))
        self._cmb_device = QComboBox()
        self._cmb_device.setMinimumHeight(30)
        audio_card.add(_form_row("Микрофон", self._cmb_device))

        # Порог энергии микрофона
        audio_card.add(_section_label("ПОРОГ ЭНЕРГИИ"))
        energy_row = QHBoxLayout()
        energy_row.setContentsMargins(0, 0, 0, 0)
        energy_row.setSpacing(8)
        self._slider_energy = QSlider(Qt.Horizontal)
        self._slider_energy.setRange(0, 100)
        self._slider_energy.setSingleStep(5)
        self._slider_energy.setValue(
            int(float(self._settings.value("energy_threshold", 0.0)) * 10000)
        )
        energy_row.addWidget(self._slider_energy, 1)
        self._lbl_energy = QLabel("0.0000")
        self._lbl_energy.setObjectName("formLabel")
        self._lbl_energy.setFixedWidth(50)
        energy_row.addWidget(self._lbl_energy)
        energy_wrap = QWidget()
        energy_wrap.setLayout(energy_row)
        audio_card.add(_form_row("Энергия", energy_wrap))
        self._slider_energy.valueChanged.connect(self._on_energy_changed)
        self._on_energy_changed(self._slider_energy.value())

        col.addWidget(audio_card)

        # === Распознавание ===================================
        thr_card = _Card()
        thr_card.add(_section_label("РАСПОЗНАВАНИЕ"))
        self._spin_fuzzy = self._make_spin()
        self._spin_vector = self._make_spin()
        self._spin_wake = self._make_spin()
        thr_card.add(_form_row("Fuzzy",  self._spin_fuzzy))
        thr_card.add(_form_row("Vector", self._spin_vector))
        thr_card.add(_form_row("Wake",   self._spin_wake))
        col.addWidget(thr_card)

        # === Слова ===========================================
        words_card = _Card()
        words_card.add(_section_label("АКТИВАЦИЯ"))
        self._edit_wake = QLineEdit()
        self._edit_wake.setPlaceholderText("акали, ассистент, компьютер")
        self._edit_reindex = QLineEdit()
        self._edit_reindex.setPlaceholderText("переиндексируй, обнови команды")
        words_card.add(_form_row("Wake",    self._edit_wake))
        words_card.add(_form_row("Реиндекс", self._edit_reindex))
        col.addWidget(words_card)

        # === LLM API =========================================
        llm_card = _Card()
        llm_card.add(_section_label("LLM (ОБЛАЧНЫЙ РОУТЕР)"))
        self._edit_gemini_key = QLineEdit()
        self._edit_gemini_key.setPlaceholderText("AIza… (необязательно — для Gemini)")
        self._edit_gemini_key.setEchoMode(QLineEdit.Password)
        self._edit_gemini_key.setText(
            str(self._settings.value("gemini_api_key", "") or ""))
        llm_card.add(_form_row("Gemini API", self._edit_gemini_key))
        col.addWidget(llm_card)

        # === Репозиторий + Обновление =============================
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

        # Полноценная панель обновления
        self._update_panel = UpdatePanel(self._repo_dir)
        self._update_panel.check_requested.connect(self.check_update_requested.emit)
        self._update_panel.update_requested.connect(self.update_requested.emit)
        repo_card.add(self._update_panel)

        col.addWidget(repo_card)

        # === Действия ========================================
        apply_card = _Card()
        self._btn_apply = QPushButton("Применить и пересобрать кэш")
        self._btn_apply.setObjectName("primaryBtn")
        self._btn_apply.setCursor(Qt.PointingHandCursor)
        self._btn_apply.setMinimumHeight(36)
        self._btn_apply.clicked.connect(self._apply)
        apply_card.add(self._btn_apply)
        col.addWidget(apply_card)

        col.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

    # ── Утилиты для билда ────────────────────────────────────
    @Slot(int)
    def _on_energy_changed(self, value: int) -> None:
        threshold = value / 10000.0
        self._lbl_energy.setText(f"{threshold:.4f}")

    def _make_spin(self) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(0.0, 1.0)
        s.setSingleStep(0.05)
        s.setDecimals(2)
        s.setMinimumHeight(28)
        return s

    def _populate_devices(self) -> None:
        self._cmb_device.clear()
        self._cmb_device.addItem("Авто (PortAudio default)", None)
        for idx, name, ch in list_input_devices():
            label = f"#{idx} · {name}  [{ch}ch]"
            if len(label) > 56:
                label = label[:55] + "…"
            self._cmb_device.addItem(label, idx)
        saved = self._settings.value("audio_device", None)
        if saved is None or saved == "":
            self._cmb_device.setCurrentIndex(0)
        else:
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
        self._spin_fuzzy.setValue(self._core.fuzzy_threshold)
        self._spin_vector.setValue(self._core.vector_threshold)
        self._spin_wake.setValue(self._core.wake_threshold)
        self._edit_wake.setText(", ".join(self._core.wake_words))
        self._edit_reindex.setText(", ".join(self._core.reindex_triggers))
        self._edit_gemini_key.setText(
            str(self._settings.value("gemini_api_key", "") or ""))

    def _apply(self) -> None:
        self._core.fuzzy_threshold = self._spin_fuzzy.value()
        self._core.vector_threshold = self._spin_vector.value()
        self._core.wake_threshold = self._spin_wake.value()
        wake = [w.strip().lower() for w in self._edit_wake.text().split(",") if w.strip()]
        reindex = [r.strip().lower() for r in self._edit_reindex.text().split(",") if r.strip()]
        if wake:
            self._core.wake_words = wake
        if reindex:
            self._core.reindex_triggers = reindex
        s = self._settings
        s.setValue("fuzzy_threshold", self._core.fuzzy_threshold)
        s.setValue("vector_threshold", self._core.vector_threshold)
        s.setValue("wake_threshold", self._core.wake_threshold)
        s.setValue("wake_words", ",".join(self._core.wake_words))
        s.setValue("reindex_triggers", ",".join(self._core.reindex_triggers))
        s.setValue("gemini_api_key", self._edit_gemini_key.text().strip())
        s.setValue("repo_dir", self._edit_repo.text().strip() or self._repo_dir)
        energy = self._slider_energy.value() / 10000.0
        s.setValue("energy_threshold", energy)
        if hasattr(self._core, "energy_threshold"):
            self._core.energy_threshold = energy
        self._repo_dir = self._edit_repo.text().strip() or self._repo_dir
        self.reload_requested.emit()

    @Slot(object)
    def on_version_info(self, info: VersionInfo) -> None:
        """Передаём результат проверки обновлений в панель обновления."""
        self._update_panel.on_version_info(info)

    @Slot(object)
    def on_update_result(self, result: UpdateResult) -> None:
        """Передаём результат git pull в панель обновления."""
        self._update_panel.on_update_result(result)

    @property
    def repo_dir(self) -> str:
        return self._edit_repo.text().strip() or self._repo_dir

    @property
    def current_device(self):
        return self._cmb_device.currentData()
