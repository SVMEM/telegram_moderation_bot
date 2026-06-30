from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Каналы и группы", callback_data="menu:channels"),
            InlineKeyboardButton(text="Правила", callback_data="menu:rules"),
        ],
        [
            InlineKeyboardButton(text="Whitelist", callback_data="menu:whitelist"),
            InlineKeyboardButton(text="Журнал", callback_data="menu:logs"),
        ],
        [
            InlineKeyboardButton(text="Статистика", callback_data="menu:stats"),
            InlineKeyboardButton(text="Статус", callback_data="menu:status"),
        ],
        [InlineKeyboardButton(text="Помощь", callback_data="menu:help")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
