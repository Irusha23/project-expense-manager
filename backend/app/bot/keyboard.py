
import re

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.models import Project

CALLBACK_TOKEN_PATTERN = re.compile(r"[0-9a-f]{12}\Z")
TELEGRAM_CALLBACK_DATA_LIMIT = 64


def project_selection_keyboard(
    projects: list[Project],
    callback_token: str,
) -> InlineKeyboardMarkup:
    if not CALLBACK_TOKEN_PATTERN.fullmatch(callback_token):
        raise ValueError("callback_token must be a 12-character hexadecimal token")

    keyboard = []

    for project in projects:
        callback_data = f"p:{callback_token}:{project.id}"
        if len(callback_data.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
            raise ValueError("project callback data exceeds Telegram's 64-byte limit")

        keyboard.append([
            InlineKeyboardButton(
                text=project.name,
                callback_data=callback_data,
            )
        ])

    cancel_callback_data = f"c:{callback_token}"
    if len(cancel_callback_data.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
        raise ValueError("cancellation callback data exceeds Telegram's 64-byte limit")

    keyboard.append([
        InlineKeyboardButton(
            text="Cancel",
            callback_data=cancel_callback_data,
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=keyboard
    )
