from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.forms import STATUS_IP, STATUS_SZ


def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="ИП", callback_data=f"status:{STATUS_IP}"),
                InlineKeyboardButton(text="Самозанятый", callback_data=f"status:{STATUS_SZ}"),
            ]
        ]
    )


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Все верно", callback_data="form:confirm"),
                InlineKeyboardButton(text="Заполнить заново", callback_data="form:restart"),
            ]
        ]
    )


def paid_keyboard(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Оплачено",
                    callback_data=f"paid:{application_id}",
                )
            ]
        ]
    )
