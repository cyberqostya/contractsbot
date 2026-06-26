from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import re
import shutil
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    Document,
    FSInputFile,
    Message,
    PhotoSize,
)
from aiogram.utils.chat_action import ChatActionSender
from aiogram.client.default import DefaultBotProperties

from bot.config import Settings, load_settings
from bot.db import Application, Database
from bot.documents import (
    DocumentError,
    amount_found_near_total,
    contract_filename,
    extract_text_from_invoice,
    render_contract,
    signed_contract_has_marks,
)
from bot.forms import fields_for_status, status_label
from bot.keyboards import confirm_keyboard, paid_keyboard, status_keyboard
from bot.states import BloggerFlow


router = Router()
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def make_link_payload(amount: str, settings: Settings) -> str:
    amount = re.sub(r"\D", "", amount)
    signature = hmac.new(
        settings.link_secret.encode("utf-8"),
        amount.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    short_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")[:10]
    return f"a_{amount}_{short_signature}"


def parse_amount_from_args(args: str | None, settings: Settings) -> str | None:
    if not args:
        return None
    match = re.search(r"(?:^|[?&\s])a_([0-9]+)_([A-Za-z0-9_-]+)", args)
    if match:
        amount = match.group(1)
        expected_payload = make_link_payload(amount, settings)
        expected_signature = expected_payload.rsplit("_", 1)[1]
        if hmac.compare_digest(match.group(2), expected_signature):
            return amount
    return None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def format_amount(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return html.escape(value)
    return f"{int(digits):,}".replace(",", ".")


def normalize_field_value(key: str, value: str) -> str:
    if key in {
        "inn",
        "ogrn",
        "bik",
        "checking_account",
        "correspondent_account",
    }:
        return re.sub(r"\D", "", value)
    if key == "passport_ser_num":
        digits = re.sub(r"\D", "", value)
        return f"{digits[:4]} {digits[4:]}"
    if key == "email":
        return value.strip().lower()
    return value


def summary_text(status: str, amount: str, requisites: dict[str, str]) -> str:
    fields = fields_for_status(status)
    lines = [
        "Проверьте данные:",
        "",
        f"Статус: <b>{status_label(status)}</b>",
        f"Сумма: <b>{format_amount(amount)} руб.</b>",
    ]
    for field in fields:
        lines.extend(
            [
                "",
                f"{field.label}:",
                f"<b>{html.escape(requisites.get(field.key, ''))}</b>",
            ]
        )
    return "\n".join(lines)


def user_link(application: Application) -> str:
    if application.username:
        return f"https://t.me/{application.username}"
    return f"tg://user?id={application.user_id}"


async def ask_next_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    status = data["status"]
    index = int(data.get("field_index", 0))
    fields = fields_for_status(status)

    if index >= len(fields):
        requisites = data.get("requisites", {})
        await state.set_state(BloggerFlow.confirming_requisites)
        await message.answer(
            summary_text(status, data["amount"], requisites),
            reply_markup=confirm_keyboard(),
        )
        return

    field = fields[index]
    text = f"{index + 1}/{len(fields)}\nВведите <b>{html.escape(field.label)}</b>"
    if field.example:
        text += f"\n<i>Пример: {html.escape(field.example)}</i>"
    await message.answer(text)


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    settings: Settings,
) -> None:
    amount = parse_amount_from_args(command.args, settings)
    await start_with_amount(message, state, amount)


@router.message(F.text.regexp(r"^/start\?a_[0-9]"))
async def start_question_mark(message: Message, state: FSMContext, settings: Settings) -> None:
    amount = parse_amount_from_args(message.text.replace("/start?", "", 1), settings)
    await start_with_amount(message, state, amount)


async def start_with_amount(message: Message, state: FSMContext, amount: str | None) -> None:
    if amount is None:
        await state.clear()
        await message.answer(
            "Бот работает только по персональной ссылке. Если вы ожидаете договор, "
            "попросите менеджера прислать вам ссылку."
        )
        return

    await state.set_state(BloggerFlow.choosing_status)
    await state.update_data(amount=amount, requisites={}, field_index=0)
    await message.answer(
        f"Сумма договора: {html.escape(amount)}\n\nВыберите ваш статус:",
        reply_markup=status_keyboard(),
    )


@router.message(Command("link"))
async def create_link(message: Message, command: CommandObject, settings: Settings, bot: Bot) -> None:
    if message.from_user.id not in settings.admin_ids:
        return
    amount = re.sub(r"\D", "", command.args or "")
    if not amount:
        await message.answer("Использование: /link 15000")
        return

    bot_info = await bot.get_me()
    payload = make_link_payload(amount, settings)
    await message.answer(
        f"Персональная ссылка на сумму <b>{format_amount(amount)} руб.</b>:\n"
        f"https://t.me/{bot_info.username}?start={payload}"
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Заполнение отменено. Можно начать заново по персональной ссылке.")


@router.callback_query(BloggerFlow.choosing_status, F.data.startswith("status:"))
async def choose_status(callback: CallbackQuery, state: FSMContext) -> None:
    status = callback.data.split(":", 1)[1]
    await state.set_state(BloggerFlow.filling_requisites)
    await state.update_data(status=status, requisites={}, field_index=0)
    await callback.message.edit_text(f"Статус: {status_label(status)}")
    await ask_next_field(callback.message, state)
    await callback.answer()


@router.message(BloggerFlow.filling_requisites, F.text)
async def fill_requisites(message: Message, state: FSMContext) -> None:
    value = clean_text(message.text)
    if not value:
        await message.answer("Похоже, поле пустое. Введите значение текстом.")
        return

    data = await state.get_data()
    status = data["status"]
    index = int(data.get("field_index", 0))
    fields = fields_for_status(status)
    field = fields[index]
    validation_error = field.validate(value)
    if validation_error:
        text = validation_error
        if field.example:
            text += f"\n<i>Пример: {html.escape(field.example)}</i>"
        await message.answer(text)
        return

    requisites = dict(data.get("requisites", {}))
    requisites[field.key] = normalize_field_value(field.key, value)

    await state.update_data(requisites=requisites, field_index=index + 1)
    await ask_next_field(message, state)


@router.message(BloggerFlow.filling_requisites)
async def reject_non_text_field(message: Message) -> None:
    await message.answer("Пожалуйста, введите значение обычным текстом.")


@router.callback_query(BloggerFlow.confirming_requisites, F.data == "form:restart")
async def restart_form(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(BloggerFlow.choosing_status)
    await state.update_data(amount=data["amount"], requisites={}, field_index=0)
    await callback.message.edit_text(
        f"Сумма договора: {html.escape(data['amount'])}\n\nВыберите ваш статус:",
        reply_markup=status_keyboard(),
    )
    await callback.answer()


@router.callback_query(BloggerFlow.confirming_requisites, F.data == "form:confirm")
async def confirm_form(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    db: Database,
) -> None:
    data = await state.get_data()
    user = callback.from_user
    requisites = data["requisites"]
    amount = data["amount"]
    status = data["status"]

    application_id = db.create_application(
        user_id=user.id,
        username=user.username,
        amount=amount,
        status=status,
        requisites=requisites,
        generated_contract_path="",
        created_at=datetime.now(MOSCOW_TZ).strftime("%H:%M %d.%m.%Y"),
    )

    await callback.message.edit_text("Генерирую договор. Это может занять несколько секунд.")
    try:
        async with ChatActionSender.upload_document(bot=callback.bot, chat_id=user.id):
            pdf_path = await asyncio.to_thread(
                render_contract,
                settings=settings,
                application_id=application_id,
                amount=amount,
                status=status,
                requisites=requisites,
            )
    except DocumentError as error:
        await callback.message.answer(f"Не удалось подготовить договор: {error}")
        await callback.answer()
        return

    await state.set_state(BloggerFlow.waiting_signed_contract)
    await state.update_data(application_id=application_id)

    await callback.message.answer_document(
        FSInputFile(pdf_path, filename=contract_filename(requisites.get("fio", ""))),
        caption=(
            "Договор готов. Подпишите его и отправьте сюда PDF-файл подписанного договора."
        ),
    )
    shutil.rmtree(pdf_path.parent, ignore_errors=True)
    await callback.answer()


@router.message(BloggerFlow.waiting_signed_contract, F.document)
async def receive_signed_contract(
    message: Message,
    state: FSMContext,
    settings: Settings,
    db: Database,
    bot: Bot,
) -> None:
    document = message.document
    if document.mime_type != "application/pdf":
        await message.answer("Подписанный договор нужно отправить PDF-файлом.")
        return

    data = await state.get_data()
    application_id = int(data["application_id"])
    signed_dir = settings.output_dir / str(application_id)
    signed_dir.mkdir(parents=True, exist_ok=True)
    signed_path = signed_dir / "signed_contract.pdf"
    await bot.download(document.file_id, destination=signed_path)
    try:
        has_signature = await asyncio.to_thread(signed_contract_has_marks, signed_path)
    finally:
        signed_path.unlink(missing_ok=True)

    if not has_signature:
        await message.answer(
            "Я не вижу в PDF признаков подписи. Проверьте, что вы подписали документ "
            "и отправьте подписанный PDF еще раз."
        )
        return

    db.update_files(application_id, signed_contract_file_id=document.file_id)
    await state.set_state(BloggerFlow.waiting_invoice)
    await message.answer("Принял договор. Теперь отправьте счет: PDF, PNG, JPG или JPEG.")


@router.message(BloggerFlow.waiting_signed_contract)
async def reject_signed_contract(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте подписанный договор PDF-файлом.")


@router.message(BloggerFlow.waiting_invoice, F.document | F.photo)
async def receive_invoice(
    message: Message,
    state: FSMContext,
    settings: Settings,
    db: Database,
    bot: Bot,
) -> None:
    data = await state.get_data()
    application_id = int(data["application_id"])
    application = db.get_application(application_id)
    if application is None:
        await message.answer("Не нашел вашу заявку. Начните заново по персональной ссылке.")
        await state.clear()
        return

    file_id, filename = invoice_file_info(message)
    if file_id is None or filename is None:
        await message.answer("Счет нужно отправить как PDF, PNG, JPG или JPEG.")
        return

    invoice_dir = settings.output_dir / str(application_id)
    invoice_dir.mkdir(parents=True, exist_ok=True)
    invoice_path = invoice_dir / filename

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        await bot.download(file_id, destination=invoice_path)
        try:
            text = await asyncio.to_thread(extract_text_from_invoice, invoice_path)
        except (DocumentError, RuntimeError) as error:
            await message.answer(f"Не удалось прочитать счет: {error}")
            return

    if not amount_found_near_total(text, application.amount):
        await message.answer(
            "Сумма счёта не соответствует сумме указанной в договоре. Проверьте счет и отправьте файл еще раз.\n"
            f"Ожидаемая сумма: {html.escape(application.amount)}"
        )
        return

    db.update_files(application_id, invoice_file_id=file_id, invoice_text=text)
    db.set_status(application_id, "waiting_admin_payment")
    await state.set_state(BloggerFlow.waiting_payment)

    updated = db.get_application(application_id)
    if updated is not None:
        await send_to_admins(bot, settings, updated)
    shutil.rmtree(invoice_dir, ignore_errors=True)

    await message.answer("Счёт принят. Документы отправлены на оплату. Оплата поступит в течение 5 рабочих дней.")


def invoice_file_info(message: Message) -> tuple[str | None, str | None]:
    if message.document:
        return document_file_info(message.document)
    if message.photo:
        photo: PhotoSize = message.photo[-1]
        return photo.file_id, "invoice.jpg"
    return None, None


def document_file_info(document: Document) -> tuple[str | None, str | None]:
    mime_to_ext = {
        "application/pdf": ".pdf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }
    suffix = mime_to_ext.get(document.mime_type or "")
    if suffix is None and document.file_name:
        raw_suffix = Path(document.file_name).suffix.lower()
        if raw_suffix in {".pdf", ".png", ".jpg", ".jpeg"}:
            suffix = raw_suffix
    if suffix is None:
        return None, None
    return document.file_id, f"invoice{suffix}"


async def send_to_admins(bot: Bot, settings: Settings, application: Application) -> None:
    fio = application.requisites.get("fio", "Без ФИО")
    caption = (
        f"Заявка от\n"
        f"<b>{html.escape(fio)}</b>\n"
        f"{html.escape(application.created_at)}\n"
        f"Сумма: <b>{format_amount(application.amount)} руб.</b>\n"
        f"{status_label(application.status)}\n"
        f"Аккаунт Telegram: {html.escape(user_link(application))}"
    )
    for admin_id in settings.admin_ids:
        await bot.send_document(
            admin_id,
            application.signed_contract_file_id,
            caption=f"{caption}\n\nПодписанный договор",
        )
        invoice_file = find_local_invoice(settings.output_dir / str(application.id))
        await bot.send_document(
            admin_id,
            FSInputFile(invoice_file) if invoice_file else application.invoice_file_id,
            caption="Счёт",
            reply_markup=paid_keyboard(application.id),
        )


def find_local_invoice(directory: Path) -> Path | None:
    for suffix in ("pdf", "png", "jpg", "jpeg"):
        path = directory / f"invoice.{suffix}"
        if path.exists():
            return path
    return None


@router.message(BloggerFlow.waiting_invoice)
async def reject_invoice(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте счет файлом PDF, PNG, JPG или JPEG.")


@router.callback_query(F.data.startswith("paid:"))
async def mark_paid(callback: CallbackQuery, settings: Settings, db: Database) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    application_id = int(callback.data.split(":", 1)[1])
    application = db.get_application(application_id)
    if application is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    db.set_status(application_id, "paid")
    await callback.bot.send_message(
        application.user_id,
        "Оплата успешно проведена. Деньги зачислятся на указанный вами счёт в ближайшее время.",
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Блогер уведомлен")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Для начала откройте бота по персональной ссылке с суммой.")


async def main() -> None:
    settings = load_settings()
    db = Database(settings.database_path)
    db.init()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage(), settings=settings, db=db)
    dispatcher.include_router(router)

    await dispatcher.start_polling(bot)
