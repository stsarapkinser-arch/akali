"""Динамическая генерация иконок для Akali (QPainter + qtawesome).

Все иконки генерируются кодом, а не загружаются из файлов PNG/SVG.
Это экономит место, ускоряет загрузку и позволяет легко менять цвета.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

try:
    import qtawesome as qta
    HAS_QTAWESOME = True
except ImportError:
    HAS_QTAWESOME = False


# ════════════════════════════════════════════════════════════════════════════════
# Цветовая палитра (Cyber Arc)
# ════════════════════════════════════════════════════════════════════════════════

COLOR_BG = QColor("#0D1117")           # Deep Dark
COLOR_SURFACE = QColor("#161B22")      # Surface
COLOR_CYAN = QColor("#00D4FF")         # Cyber Cyan (Arc Reactor)
COLOR_BLUE = QColor("#1D8EE6")         # Kali Blue
COLOR_TEXT = QColor("#E6EDF3")         # Light Text
COLOR_MUTED = QColor("#8B949E")        # Muted
COLOR_GREEN = QColor("#3FB950")        # Success
COLOR_RED = QColor("#F85149")          # Error
COLOR_YELLOW = QColor("#D29922")       # Warning


# ════════════════════════════════════════════════════════════════════════════════
# Примитивы для рисования
# ════════════════════════════════════════════════════════════════════════════════

def _create_pixmap(size: int = 24, background: QColor | None = None) -> tuple[QPixmap, QPainter]:
    """Создаёт пиксмап и QPainter для рисования.

    Args:
        size: размер иконки в пикселях
        background: цвет фона (по умолчанию прозрачный)

    Returns:
        (pixmap, painter) готовые к рисованию
    """
    pixmap = QPixmap(size, size)
    if background:
        pixmap.fill(background)
    else:
        pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    return pixmap, painter


def circle_icon(
    size: int = 24,
    fill_color: QColor = COLOR_CYAN,
    border_color: QColor | None = None,
) -> QIcon:
    """Иконка-окружность (для статусов, кнопок)."""
    pixmap, painter = _create_pixmap(size)

    margin = 2
    painter.fillEllipse(margin, margin, size - 2*margin, size - 2*margin, fill_color)

    if border_color:
        pen = QPen(border_color, 1)
        painter.setPen(pen)
        painter.drawEllipse(margin, margin, size - 2*margin, size - 2*margin)

    painter.end()
    return QIcon(pixmap)


def microphone_icon(
    size: int = 24,
    color: QColor = COLOR_TEXT,
    active: bool = False,
) -> QIcon:
    """Иконка микрофона."""
    pixmap, painter = _create_pixmap(size)

    # Корпус микрофона
    mic_width = size // 3
    mic_height = size // 2
    mic_x = (size - mic_width) // 2
    mic_y = size // 4

    if active:
        painter.fillRect(mic_x, mic_y, mic_width, mic_height, COLOR_CYAN)
    else:
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawRect(mic_x, mic_y, mic_width, mic_height)

    # Ножка микрофона
    painter.setPen(QPen(color, 2))
    painter.drawLine(size // 2, mic_y + mic_height, size // 2, size - 4)

    # Основание
    painter.drawEllipse((size - mic_width) // 2, size - 6, mic_width, 4)

    painter.end()
    return QIcon(pixmap)


def settings_icon(
    size: int = 24,
    color: QColor = COLOR_TEXT,
) -> QIcon:
    """Иконка шестерёнки (настройки)."""
    pixmap, painter = _create_pixmap(size)

    center = size // 2
    radius = size // 3
    teeth = 8

    pen = QPen(color, 2)
    painter.setPen(pen)

    # Центральное колесо
    painter.drawEllipse(center - radius, center - radius, 2*radius, 2*radius)

    # Зубцы шестерёнки
    import math
    for i in range(teeth):
        angle = (i * 360 / teeth) * math.pi / 180
        x1 = center + (radius + 2) * math.cos(angle)
        y1 = center + (radius + 2) * math.sin(angle)
        x2 = center + (radius + 6) * math.cos(angle)
        y2 = center + (radius + 6) * math.sin(angle)
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    # Внутреннее отверстие
    painter.drawEllipse(center - 4, center - 4, 8, 8)

    painter.end()
    return QIcon(pixmap)


def play_icon(
    size: int = 24,
    color: QColor = COLOR_CYAN,
) -> QIcon:
    """Иконка воспроизведения (треугольник)."""
    pixmap, painter = _create_pixmap(size)

    margin = 4
    polygon = QPolygonF([
        QPointF(margin, margin),
        QPointF(margin, size - margin),
        QPointF(size - margin, size // 2),
    ])

    painter.setBrush(color)
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(polygon)
    painter.end()
    return QIcon(pixmap)


def stop_icon(
    size: int = 24,
    color: QColor = COLOR_RED,
) -> QIcon:
    """Иконка остановки (квадрат)."""
    pixmap, painter = _create_pixmap(size)

    margin = 4
    painter.fillRect(margin, margin, size - 2*margin, size - 2*margin, color)
    painter.end()
    return QIcon(pixmap)


def home_icon(
    size: int = 24,
    color: QColor = COLOR_TEXT,
) -> QIcon:
    """Иконка дома (крыша + квадрат)."""
    pixmap, painter = _create_pixmap(size)

    # Крыша (треугольник) — заливка через QPolygonF
    roof = QPolygonF([
        QPointF(size // 2, 4),
        QPointF(size - 4, 12),
        QPointF(4, 12),
    ])
    painter.setBrush(color)
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(roof)

    # Стены (квадрат)
    pen = QPen(color, 2)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(4, 12, size - 8, size - 16)

    # Дверь
    painter.drawRect(size // 2 - 3, 16, 6, 8)

    painter.end()
    return QIcon(pixmap)


def commands_icon(
    size: int = 24,
    color: QColor = COLOR_TEXT,
) -> QIcon:
    """Иконка команд (список строк)."""
    pixmap, painter = _create_pixmap(size)

    pen = QPen(color, 1.5)
    painter.setPen(pen)

    line_height = size // 5
    for i in range(4):
        y = 4 + i * line_height
        painter.drawLine(4, y, size - 4, y)

    painter.end()
    return QIcon(pixmap)


def log_icon(
    size: int = 24,
    color: QColor = COLOR_TEXT,
) -> QIcon:
    """Иконка логов (документ)."""
    pixmap, painter = _create_pixmap(size)

    pen = QPen(color, 1.5)
    painter.setPen(pen)

    # Документ
    painter.drawRect(4, 2, size - 8, size - 4)

    # Строки текста
    painter.drawLine(6, 8, size - 6, 8)
    painter.drawLine(6, 13, size - 6, 13)
    painter.drawLine(6, 18, size - 10, 18)

    painter.end()
    return QIcon(pixmap)


def reactor_icon(
    size: int = 48,
    color: QColor = COLOR_CYAN,
) -> QIcon:
    """Arc Reactor иконка (минималистичная светящаяся окружность)."""
    pixmap, painter = _create_pixmap(size, COLOR_BG)

    center = size // 2
    radius = size // 3

    # Внешние кольца (свечение)
    for i in range(3):
        r = radius + i * 4
        alpha = int(255 * (1 - i / 3))
        ring_color = QColor(color)
        ring_color.setAlpha(alpha)
        pen = QPen(ring_color, 2)
        painter.setPen(pen)
        painter.drawEllipse(center - r, center - r, 2*r, 2*r)

    # Центральная сфера (активная)
    painter.fillEllipse(center - radius // 2, center - radius // 2,
                        radius, radius, color)

    # Подсветка от сферы
    highlight_color = QColor(255, 255, 255, 100)
    painter.fillEllipse(center - radius // 3, center - radius // 3,
                        radius // 2, radius // 2, highlight_color)

    painter.end()
    return QIcon(pixmap)


def led_icon(
    size: int = 12,
    color: QColor = COLOR_GREEN,
    active: bool = True,
) -> QIcon:
    """LED индикатор."""
    pixmap, painter = _create_pixmap(size, COLOR_BG)

    if active:
        painter.fillEllipse(1, 1, size - 2, size - 2, color)
        # Свечение
        glow_color = QColor(color)
        glow_color.setAlpha(50)
        painter.fillEllipse(0, 0, size, size, glow_color)
    else:
        pen = QPen(COLOR_MUTED, 1)
        painter.setPen(pen)
        painter.drawEllipse(1, 1, size - 2, size - 2)

    painter.end()
    return QIcon(pixmap)


def get_qtawesome_icon(
    name: str,
    size: int = 24,
    color: str = "#00D4FF",
) -> QIcon | None:
    """Получить иконку из qtawesome (если доступен).

    Args:
        name: имя иконки (e.g. 'fa.microphone', 'fa.cog')
        size: размер в пикселях
        color: hex цвет

    Returns:
        QIcon или None если qtawesome не установлен
    """
    if not HAS_QTAWESOME:
        return None
    try:
        return qta.icon(name, color=color)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════════
# Фасад для быстрого доступа
# ════════════════════════════════════════════════════════════════════════════════

class IconSet:
    """Набор всех иконок приложения."""

    @staticmethod
    def microphone_active() -> QIcon:
        return microphone_icon(24, COLOR_CYAN, active=True)

    @staticmethod
    def microphone_inactive() -> QIcon:
        return microphone_icon(24, COLOR_MUTED, active=False)

    @staticmethod
    def play() -> QIcon:
        return play_icon(24, COLOR_GREEN)

    @staticmethod
    def stop() -> QIcon:
        return stop_icon(24, COLOR_RED)

    @staticmethod
    def settings() -> QIcon:
        return settings_icon(24, COLOR_TEXT)

    @staticmethod
    def home() -> QIcon:
        return home_icon(24, COLOR_TEXT)

    @staticmethod
    def commands() -> QIcon:
        return commands_icon(24, COLOR_TEXT)

    @staticmethod
    def log() -> QIcon:
        return log_icon(24, COLOR_TEXT)

    @staticmethod
    def reactor(size: int = 48) -> QIcon:
        return reactor_icon(size, COLOR_CYAN)

    @staticmethod
    def led_green() -> QIcon:
        return led_icon(12, COLOR_GREEN, active=True)

    @staticmethod
    def led_yellow() -> QIcon:
        return led_icon(12, COLOR_YELLOW, active=True)

    @staticmethod
    def led_red() -> QIcon:
        return led_icon(12, COLOR_RED, active=True)

    @staticmethod
    def led_off() -> QIcon:
        return led_icon(12, COLOR_MUTED, active=False)

    @staticmethod
    def tray_icon() -> QIcon:
        """Иконка для системного трея (Arc Reactor)."""
        return reactor_icon(32, COLOR_CYAN)
