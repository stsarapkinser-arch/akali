# Akali UI/UX Refactoring — Comprehensive Guide

## 📋 Overview

This refactoring modernizes the Akali interface with:
- ✅ **Frameless, draggable window** (Wayland-compatible)
- ✅ **Dark theme** (Cyber Arc: Kali Blue + Cyan)
- ✅ **Dynamic icon generation** (no external PNG/SVG files)
- ✅ **Arc Reactor visualization** on home page
- ✅ **Card-based settings UI** with toggle switches
- ✅ **System tray integration** (minimize to tray)
- ✅ **Memory optimization** for Intel N100 (CPU-bound cleanup)
- ✅ **Modern header** with drag-to-move support

---

## 🎨 New Files Created

### 1. **akali/ui/app.qss** — Global Stylesheet
Complete dark theme with:
- Color palette: `#0D1117` (bg), `#00D4FF` (cyan), `#1D8EE6` (kali blue)
- Modern components: buttons, cards, inputs, scrollbars, tooltips
- Smooth transitions and hover effects
- Optimized for weak hardware (no heavy shadows/gradients)

### 2. **akali/ui/icons.py** — Dynamic Icon Generation
- Uses **QPainter** primitives (no external files)
- `IconSet` class with static methods for quick access
- Supports qtawesome fallback (if installed)
- Icons: microphone, settings, play, stop, home, commands, log, reactor, LEDs

**Usage:**
```python
from akali.ui.icons import IconSet, COLOR_CYAN

btn_play = QPushButton("Play")
btn_play.setIcon(IconSet.play())
```

### 3. **akali/ui/widgets/modern_header.py** — Frameless Header
`ModernHeaderBar` class:
- Drag-to-move window by clicking/dragging header
- Dynamic tab buttons with active state
- Version display
- No system frame needed

**Usage:**
```python
header = ModernHeaderBar("0.1.0", parent=window)
header.tab_clicked.connect(lambda key: print(f"Tab: {key}"))
header.set_active("commands")
```

### 4. **akali/ui/pages/home_page_new.py** — Modern Home Page
New `HomePage` class with:
- **ReactorWidget**: Pulsing Arc Reactor (center)
- **InfoPanel**: Microphone, model, RAM, ping (bottom)
- Control buttons: Start, Stop, Reindex
- Spoken text display (real-time)
- State management (listening, processing, error)

**Usage:**
```python
home = HomePage()
home.set_state("listening")
home.show_spoken("turn off lights")
home.set_level(0.75)
```

### 5. **akali/ui/pages/settings_page_new.py** — Card-Based Settings
New `SettingsPage` class with:
- **CardFrame**: Reusable card widget with title/description
- **ToggleSwitch**: Custom checkbox (better UX)
- Groups: AI Models, Audio, System
- Scroll area for mobile-like responsiveness
- Action buttons: Reload, Update

**Usage:**
```python
settings = SettingsPage(core, qsettings, repo_dir)
settings.reload_requested.connect(on_reload)
```

---

## 🔄 Migration Steps

### Step 1: Update main_window.py
The provided refactored `main_window.py` includes:
- Frameless window support (`Qt.FramelessWindowHint`)
- Modern header integration (`ModernHeaderBar`)
- QSS stylesheet loading
- System tray integration (closeEvent override)
- Memory cleanup (`_cleanup` method)

✅ **Already updated** — just check imports match.

### Step 2: Replace HomePage
**OLD:** `akali/ui/pages/home_page.py` (existing)
**NEW:** `akali/ui/pages/home_page_new.py` (provided)

Option A: Direct replacement (simple):
```bash
cp akali/ui/pages/home_page.py akali/ui/pages/home_page_old.py
cp akali/ui/pages/home_page_new.py akali/ui/pages/home_page.py
```

Option B: Merge existing logic with new UI (recommended):
- Keep existing `HomePage` class
- Add `ReactorWidget` and `InfoPanel` classes from new version
- Update `_build()` to use new layout

### Step 3: Replace SettingsPage
**OLD:** `akali/ui/pages/settings_page.py` (existing)
**NEW:** `akali/ui/pages/settings_page_new.py` (provided)

- Merge existing settings logic with new card-based UI
- Keep signal connections same
- Update control names (e.g., `btn_reload` instead of old names)

### Step 4: Update widgets/__init__.py
Already updated to include `ModernHeaderBar`:
```python
from .modern_header import ModernHeaderBar
__all__ = ["HeaderBar", "ModernHeaderBar", "Reactor", "StatusRow"]
```

### Step 5: Load Stylesheet in main.py
In `AkaliApp.__init__()` or `main()`:
```python
app.setStyleSheet(load_stylesheet())

def load_stylesheet() -> str:
    qss_path = Path(__file__).parent / "ui" / "app.qss"
    return qss_path.read_text(encoding="utf-8")
```

---

## 🖼️ Color Palette Reference

| Variable | Value | Usage |
|----------|-------|-------|
| `COLOR_BG` | `#0D1117` | Main background |
| `COLOR_SURFACE` | `#161B22` | Cards, panels |
| `COLOR_CYAN` | `#00D4FF` | Arc Reactor, active states |
| `COLOR_BLUE` | `#1D8EE6` | Kali Blue, buttons |
| `COLOR_TEXT` | `#E6EDF3` | Main text |
| `COLOR_MUTED` | `#8B949E` | Disabled, hints |
| `COLOR_GREEN` | `#3FB950` | Success, active LEDs |
| `COLOR_RED` | `#F85149` | Error, stop button |
| `COLOR_YELLOW` | `#D29922` | Warning |

All colors available in `icons.py` as `QColor` objects:
```python
from akali.ui.icons import COLOR_CYAN, COLOR_BLUE

label.setStyleSheet(f"color: {COLOR_CYAN.name()};")
```

---

## ⚡ Performance Optimization

### Memory Cleanup
Auto-delete widgets on window close:
```python
def _cleanup(self) -> None:
    self.home_page.deleteLater()
    # ... other pages
```

### Framerate Control
ReactorWidget pulsing animation: **20 FPS** (50ms interval)
```python
self._pulse_timer.setInterval(50)  # 20 FPS, not 60
```

### Icon Caching
Icons generated once, cached by reference:
```python
reactor_icon = IconSet.reactor()  # Generated once
btn1.setIcon(reactor_icon)
btn2.setIcon(reactor_icon)  # Reused, not regenerated
```

### No External Files
All icons/images generated in code → faster load, no disk I/O

---

## 🎯 Feature Checklist

- [x] Frameless window with drag-to-move header
- [x] Modern dark theme (Cyber Arc)
- [x] Dynamic icon generation (no PNG/SVG)
- [x] Arc Reactor visualization
- [x] Info panel (mic, model, RAM, ping)
- [x] Card-based settings UI
- [x] Custom toggle switches
- [x] System tray support (closeEvent)
- [x] Memory optimization (deleteLater)
- [x] Responsive layout (scroll areas)
- [x] QSS stylesheet with smooth transitions
- [x] Tooltip support (built into QSS)

---

## 📱 Usage Examples

### Switching Pages
```python
# From anywhere in the app
window.header.tab_clicked.emit("commands")
window._switch_page("settings")
```

### Updating Status
```python
# From AudioWorker
window.on_status("listening")
window.on_text("turn on the lights")
window.on_level(0.85)
```

### Showing Info
```python
# Update info panel
home_page.info_panel.set_mic_status("PipeWire: ALC269")
home_page.info_panel.set_model_status("all-minilm-l12-v2")
home_page.info_panel.set_ram_status("2.4 GB / 8.0 GB")
```

### Custom Buttons
```python
from akali.ui.icons import IconSet

btn = QPushButton("Custom")
btn.setIcon(IconSet.microphone_active())
btn.setObjectName("primaryButton")  # Applies QSS styling
window.setCentralWidget(btn)
```

---

## 🔧 Troubleshooting

### QSS Not Loading
Check file path in `main_window.py._load_stylesheet()`:
```python
qss_path = Path(__file__).replace("main_window.py", "app.qss")
```

### Icons Look Bad
Ensure `qtawesome` is NOT required (fallback works):
```python
from akali.ui.icons import HAS_QTAWESOME
if HAS_QTAWESOME:
    print("Using qtawesome")
else:
    print("Using QPainter fallback")
```

### Drag-to-Move Not Working
Ensure `ModernHeaderBar` is parent of window buttons.
If buttons steal mouse events, override in button's `mousePressEvent()`:
```python
class MyButton(QPushButton):
    def mousePressEvent(self, event):
        event.accept()  # Don't propagate to header
        super().mousePressEvent(event)
```

### Tray Icon Missing
Ensure `QSystemTrayIcon` is created before `MainWindow`:
```python
tray = TrayController(icon, qapp)
window = MainWindow(core, settings, repo_dir, icon=icon)
```

---

## 📚 Additional Resources

- **Qt Stylesheet Reference**: https://doc.qt.io/qt-6/stylesheet-reference.html
- **QPainter Guide**: https://doc.qt.io/qt-6/qpainter.html
- **PySide6 Docs**: https://doc.qt.io/qt-for-python-6/

---

## ✨ Next Steps

1. **Test frameless window** on Wayland (KDE Plasma 6)
2. **Verify icon rendering** on Intel N100 (CPU-only)
3. **Benchmark memory usage** vs. old UI
4. **Gather user feedback** on new design
5. **Optimize further** if needed (e.g., reduce animation FPS even more)

Good luck! 🚀
