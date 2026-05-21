#!/usr/bin/env python3
"""Точка входа в Akali.

Сам по себе тонкий шим: вся логика — в `akali.app:main`.

Запуск:
    python3 akali.py
"""
from akali.app import main

if __name__ == "__main__":
    raise SystemExit(main())
