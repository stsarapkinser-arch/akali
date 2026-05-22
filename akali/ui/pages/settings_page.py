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
                                QHBoxLayout, QLabel, QLineEdit, QPushButton,
                                QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ...core.backend import AssistantCore
from ...core.audio_worker import list_input_devices


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

        # === Репозиторий =====================================
        repo_card = _Card()
        repo_card.add(_section_label("РЕПОЗИТОРИЙ"))
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
        self._btn_update = QPushButton("Обновить из репо (git pull)")
        self._btn_update.setObjectName("secondaryBtn")
        self._btn_update.setCursor(Qt.PointingHandCursor)
        self._btn_update.clicked.connect(self.update_requested.emit)
        repo_card.add(self._btn_update)
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
        self._repo_dir = self._edit_repo.text().strip() or self._repo_dir
        self.reload_requested.emit()

    @property
    def repo_dir(self) -> str:
        return self._edit_repo.text().strip() or self._repo_dir

    @property
    def current_device(self):
        return self._cmb_device.currentData()
