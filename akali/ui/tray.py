"""Системный трей-иконка для Akali.

Меню в трее: Старт/Стоп, Переиндексировать, Обновить из репо,
Показать окно, Выход. Сигналы пробрасываются наружу.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class TrayController(QObject):
    """Обёртка над QSystemTrayIcon с сигналами для UI/AudioWorker."""

    start_clicked = Signal()
    stop_clicked = Signal()
    reindex_clicked = Signal()
    update_clicked = Signal()
    show_window_clicked = Signal()
    quit_clicked = Signal()

    def __init__(self, icon: QIcon, app: QApplication, parent: QObject | None = None):
        super().__init__(parent)
        self.tray = QSystemTrayIcon(icon, app)
        self.tray.setToolTip("Akali — голосовой ассистент")

        menu = QMenu()
        self._act_show = QAction("Показать окно", self)
        self._act_show.triggered.connect(self.show_window_clicked.emit)
        menu.addAction(self._act_show)

        menu.addSeparator()
        self._act_start = QAction("Слушать", self)
        self._act_start.triggered.connect(self.start_clicked.emit)
        menu.addAction(self._act_start)

        self._act_stop = QAction("Остановить", self)
        self._act_stop.triggered.connect(self.stop_clicked.emit)
        self._act_stop.setEnabled(False)
        menu.addAction(self._act_stop)

        self._act_reindex = QAction("Переиндексировать систему", self)
        self._act_reindex.triggered.connect(self.reindex_clicked.emit)
        menu.addAction(self._act_reindex)

        self._act_update = QAction("Обновить из репо (git pull)", self)
        self._act_update.triggered.connect(self.update_clicked.emit)
        menu.addAction(self._act_update)

        menu.addSeparator()
        self._act_quit = QAction("Выход", self)
        self._act_quit.triggered.connect(self.quit_clicked.emit)
        menu.addAction(self._act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.show_window_clicked.emit()

    def set_listening(self, is_listening: bool):
        self._act_start.setEnabled(not is_listening)
        self._act_stop.setEnabled(is_listening)

    def show_message(self, title: str, body: str,
                     icon=QSystemTrayIcon.Information, msecs: int = 4000):
        self.tray.showMessage(title, body, icon, msecs)
