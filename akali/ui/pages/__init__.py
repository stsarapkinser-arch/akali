"""Страницы главного окна — по одной на «вкладку»."""
from .commands_page import CommandsPage
from .home_page import HomePage
from .settings_page import SettingsPage

__all__ = ["HomePage", "CommandsPage", "SettingsPage"]
