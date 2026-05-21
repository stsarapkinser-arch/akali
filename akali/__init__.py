"""Akali — голосовой ассистент для KDE Plasma 6 / Kali Linux.

Структура пакета:
    akali/
        app.py              координатор приложения (AkaliApp)
        paths.py            пути до всех файлов проекта
        core/               чистая бизнес-логика (без Qt)
            db.py           парсинг commands.txt и auto_commands.json
            matcher.py      fuzzy + vector + cosine
            cache.py        дисковый кэш эмбеддингов
            executor.py     запуск subprocess
            backend.py      фасад AssistantCore поверх всего выше
            audio_worker.py QThread с микрофоном + Vosk
            updater.py      git pull --ff-only
        ui/                 GUI на PySide6
            main_window.py
            tray.py
            pages/          по одной странице на вкладку
            widgets/        переиспользуемые виджеты (реактор, статус-бар)
            resources/      app.qss, icon.svg
"""
__version__ = "0.3.0"
