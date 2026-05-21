"""Страница «Настройки»: пороги, wake-words, путь к репо, git-обновление."""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QFormLayout,
                                QGroupBox, QHBoxLayout, QLineEdit, QPushButton,
                                QVBoxLayout, QWidget)

from ...core.backend import AssistantCore


class SettingsPage(QWidget):
    """Пороги fuzzy/vector/wake + wake-words + путь к репо + git pull."""

    update_requested = Signal()
    reload_requested = Signal()

    def __init__(self, core: AssistantCore, settings: QSettings,
                 repo_dir: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir
        self._build()
        self._load_into_widgets()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        # --- Пороги распознавания ---------------------------------
        thr = QGroupBox("Пороги распознавания")
        thr_form = QFormLayout(thr)
        thr_form.setLabelAlignment(Qt.AlignLeft)
        self._spin_fuzzy = self._make_spin()
        self._spin_vector = self._make_spin()
        self._spin_wake = self._make_spin()
        thr_form.addRow("Fuzzy (текст)", self._spin_fuzzy)
        thr_form.addRow("Vector (смысл)", self._spin_vector)
        thr_form.addRow("Wake-word", self._spin_wake)
        outer.addWidget(thr)

        # --- Wake-words / реиндекс-фразы --------------------------
        words = QGroupBox("Активационные слова и реиндекс")
        wform = QFormLayout(words)
        wform.setLabelAlignment(Qt.AlignLeft)
        self._edit_wake = QLineEdit()
        self._edit_wake.setPlaceholderText("через запятую: акали, ассистент, компьютер")
        self._edit_reindex = QLineEdit()
        self._edit_reindex.setPlaceholderText("через запятую: переиндексируй, обнови команд")
        wform.addRow("Wake-words", self._edit_wake)
        wform.addRow("Реиндекс-фразы", self._edit_reindex)
        outer.addWidget(words)

        # --- Путь к репо ------------------------------------------
        paths = QGroupBox("Пути")
        pform = QFormLayout(paths)
        pform.setLabelAlignment(Qt.AlignLeft)
        self._edit_repo = QLineEdit(self._repo_dir)
        repo_row = QHBoxLayout()
        repo_row.setContentsMargins(0, 0, 0, 0)
        repo_row.addWidget(self._edit_repo, 1)
        browse = QPushButton("…")
        browse.setMaximumWidth(40)
        browse.setObjectName("secondaryBtn")
        browse.clicked.connect(self._browse_repo)
        repo_row.addWidget(browse)
        wrap = QWidget()
        wrap.setLayout(repo_row)
        pform.addRow("Папка репозитория", wrap)
        outer.addWidget(paths)

        # --- Действия ---------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self._btn_apply = QPushButton("Применить и пересобрать кэш")
        self._btn_apply.setObjectName("primaryBtn")
        self._btn_apply.setCursor(Qt.PointingHandCursor)
        self._btn_apply.clicked.connect(self._apply)
        actions.addWidget(self._btn_apply)

        self._btn_update = QPushButton("Обновить из репо (git pull)")
        self._btn_update.setObjectName("secondaryBtn")
        self._btn_update.setCursor(Qt.PointingHandCursor)
        self._btn_update.clicked.connect(self.update_requested.emit)
        actions.addWidget(self._btn_update)
        actions.addStretch(1)
        outer.addLayout(actions)

        outer.addStretch(1)

    def _make_spin(self) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(0.0, 1.0)
        s.setSingleStep(0.05)
        s.setDecimals(2)
        return s

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
        s.setValue("repo_dir", self._edit_repo.text().strip() or self._repo_dir)
        self._repo_dir = self._edit_repo.text().strip() or self._repo_dir
        self.reload_requested.emit()

    @property
    def repo_dir(self) -> str:
        return self._edit_repo.text().strip() or self._repo_dir
