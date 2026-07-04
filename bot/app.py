from __future__ import annotations

import asyncio
import html
import re
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Document, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonDefault, MenuButtonWebApp, Message, PhotoSize, WebAppInfo
from aiogram.utils.chat_action import ChatActionSender
from aiogram.client.default import DefaultBotProperties

from bot.config import Settings, load_settings
from bot.db import Application, Database, PersonalLink
from bot.documents import (
    DocumentError,
    amount_found_near_total,
    check_filename,
    contract_text_contains_amount,
    extract_text_from_invoice,
    extract_text_from_pdf,
    file_sha256,
    signed_contract_has_marks,
)
from bot.forms import status_label
from bot.web import start_web_server


router = Router()
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def parse_token_from_args(args: str | None) -> str | None:
    if not args:
        return None
    token = args.strip().split()[0]
    if re.fullmatch(r"[A-Za-z0-9_-]{16,80}", token):
        return token
    return None


def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)


def iso_moscow(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def format_amount(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return html.escape(value)
    return f"{int(digits):,}".replace(",", ".")


def admin_web_app_url(settings: Settings, amount: str | None = None) -> str:
    url = f"{settings.webapp_base_url}/admin"
    query: dict[str, str] = {}
    if amount:
        query["amount"] = amount
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def admin_web_app_keyboard(settings: Settings, amount: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Создать новую заявку",
                    web_app=WebAppInfo(url=admin_web_app_url(settings, amount)),
                )
            ]
        ]
    )


def user_web_app_url(settings: Settings, token: str) -> str:
    return f"{settings.webapp_base_url}/form?{urlencode({'token': token})}"


def user_web_app_keyboard(settings: Settings, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Заполнить данные",
                    web_app=WebAppInfo(url=user_web_app_url(settings, token)),
                )
            ]
        ]
    )


def user_link(application: Application) -> str:
    if application.username:
        return f"https://t.me/{application.username}"
    return f"tg://user?id={application.user_id}"


@router.message(CommandStart())
async def start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    settings: Settings,
    db: Database,
) -> None:
    token = parse_token_from_args(command.args)
    if token is None and message.from_user.id in settings.admin_ids:
        await state.clear()
        await send_admin_home(message, settings)
        return
    if message.from_user.id not in settings.admin_ids:
        await reset_user_menu_button(message.bot, message.from_user.id)
    await start_with_link(message, state, settings, db, token)


@router.message(F.text.regexp(r"^/start\?[A-Za-z0-9_-]"))
async def start_question_mark(message: Message, state: FSMContext, settings: Settings, db: Database) -> None:
    token = parse_token_from_args(message.text.replace("/start?", "", 1))
    await start_with_link(message, state, settings, db, token)


async def send_admin_home(message: Message, settings: Settings) -> None:
    if settings.webapp_base_url:
        await set_admin_menu_button(message.bot, message.from_user.id, settings)
        await message.answer(
            "Админ-панель готова. Нажмите кнопку меню внизу чата или кнопку ниже, чтобы создать новую заявку.",
            reply_markup=admin_web_app_keyboard(settings),
        )
        return

    await message.answer(
        "Админ-панель не подключена. Укажите WEBAPP_BASE_URL в .env, чтобы открыть веб-форму из меню Telegram."
    )


async def start_with_link(
    message: Message,
    state: FSMContext,
    settings: Settings,
    db: Database,
    token: str | None,
) -> None:
    now = now_moscow()
    db.delete_expired_links(iso_moscow(now))
    link = db.get_personal_link(token) if token else None
    if link is None:
        await state.clear()
        await message.answer(
            "Бот работает только по персональной ссылке. Если вы ожидаете договор, "
            "попросите менеджера прислать вам ссылку."
        )
        return
    if link.used_at is not None:
        await state.clear()
        await message.answer("Эта персональная ссылка уже использована.")
        return
    if datetime.fromisoformat(link.expires_at) < now:
        await state.clear()
        await message.answer("Срок действия персональной ссылки истек. Попросите менеджера создать новую.")
        return
    if not settings.webapp_base_url:
        await state.clear()
        await message.answer("Форма временно недоступна. Попросите менеджера проверить настройки бота.")
        return

    await state.clear()
    await message.answer(
        "Здравствуйте! Заполните, пожалуйста, ваши данные для создания договора "
        f"на оказание услуг на сумму {html.escape(format_amount(link.amount))} руб.",
        reply_markup=user_web_app_keyboard(settings, link.token),
    )


async def send_tz_files_to_user(
    bot: Bot,
    user_id: int,
    settings: Settings,
    link: PersonalLink,
) -> None:
    for file_name in link.template_files:
        path = settings.tztemplates_dir / file_name
        if path.is_file():
            await bot.send_document(user_id, FSInputFile(path), caption="Техническое задание для рекламы")


@router.message(F.document)
async def receive_signed_contract(
    message: Message,
    settings: Settings,
    db: Database,
    bot: Bot,
) -> None:
    application = db.latest_with_payment_status_for_user(
        message.from_user.id,
        "waiting_signed_contract",
    )
    if application is None:
        await receive_invoice(message, settings, db, bot)
        return

    document = message.document
    if document.mime_type != "application/pdf":
        await message.answer("Подписанный договор нужно отправить PDF-файлом.")
        return

    application_id = application.id
    signed_dir = settings.output_dir / str(application_id)
    signed_dir.mkdir(parents=True, exist_ok=True)
    signed_path = signed_dir / "signed_contract.pdf"
    await bot.download(document.file_id, destination=signed_path)
    try:
        try:
            uploaded_hash = await asyncio.to_thread(file_sha256, signed_path)
            if application.generated_contract_sha256 and uploaded_hash == application.generated_contract_sha256:
                await message.answer(
                    "Я не вижу изменений в договоре. Похоже, вы отправили тот же PDF, который я сформировал. "
                    "Подпишите договор и отправьте измененный PDF еще раз."
                )
                return

            has_signature = await asyncio.to_thread(signed_contract_has_marks, signed_path)
            signed_text = await asyncio.to_thread(extract_text_from_pdf, signed_path)
        except Exception as error:
            await message.answer(f"Не удалось проверить подписанный договор: {error}")
            return
    finally:
        signed_path.unlink(missing_ok=True)

    if not has_signature:
        await message.answer(
            "Я не вижу в PDF признаков подписи. Проверьте, что вы подписали документ "
            "и отправьте подписанный PDF еще раз."
        )
        return

    if not contract_text_contains_amount(signed_text, application.amount):
        await message.answer(
            "В подписанном договоре не вижу исходную сумму договора. "
            "Проверьте, что сумма не изменилась, и отправьте PDF еще раз.\n"
            f"Ожидаемая сумма: {html.escape(format_amount(application.amount))} руб."
        )
        return

    db.update_files(application_id, signed_contract_file_id=document.file_id)
    db.set_status(application_id, "waiting_invoice")
    await message.answer("Принял договор. Теперь отправьте счет в формате PDF, PNG или JPG")


@router.message(F.document | F.photo)
async def receive_invoice(
    message: Message,
    settings: Settings,
    db: Database,
    bot: Bot,
) -> None:
    signed_application = db.latest_with_payment_status_for_user(
        message.from_user.id,
        "waiting_signed_contract",
    )
    if signed_application is not None:
        await message.answer("Пожалуйста, отправьте подписанный договор PDF-файлом.")
        return

    application = db.latest_with_payment_status_for_user(
        message.from_user.id,
        "waiting_invoice",
    )
    if application is None:
        await message.answer("Для начала откройте бота по персональной ссылке с суммой.")
        return
    application_id = application.id

    file_id, filename = invoice_file_info(message)
    if file_id is None or filename is None:
        await message.answer("Счет нужно отправить как PDF, PNG, JPG или JPEG.")
        return

    invoice_dir = settings.output_dir / str(application_id)
    invoice_dir.mkdir(parents=True, exist_ok=True)
    invoice_path = invoice_dir / check_filename(application.requisites.get("fio", ""), Path(filename).suffix)

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        await bot.download(file_id, destination=invoice_path)
        try:
            text = await asyncio.to_thread(extract_text_from_invoice, invoice_path)
        except (DocumentError, RuntimeError) as error:
            invoice_path.unlink(missing_ok=True)
            await message.answer(f"Не удалось прочитать счет: {error}")
            return

    if not amount_found_near_total(text, application.amount):
        invoice_path.unlink(missing_ok=True)
        await message.answer(
            "Сумма счёта не соответствует сумме указанной в договоре. Проверьте счет и отправьте файл еще раз.\n"
            f"Ожидаемая сумма: {html.escape(application.amount)}"
        )
        return

    db.update_files(application_id, invoice_file_id=file_id, invoice_text=text)
    db.set_status(application_id, "completed")
    updated = db.get_application(application_id)
    personal_link = db.get_personal_link_by_application_id(application_id)
    if updated is not None:
        await send_to_admins(bot, settings, updated)
    shutil.rmtree(invoice_dir, ignore_errors=True)

    await message.answer("Счёт принят. Документы переданы менеджеру.")
    if personal_link is not None:
        await send_tz_files_to_user(bot, message.from_user.id, settings, personal_link)


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


async def send_to_admins(
    bot: Bot,
    settings: Settings,
    application: Application,
) -> None:
    fio = application.requisites.get("fio", "Без ФИО")
    nickname = application.requisites.get("nickname", "").strip()
    author_lines = ["Документы от"]
    if nickname:
        author_lines.append(f"<b>{html.escape(nickname)}</b>")
    author_lines.append(f"<b>{html.escape(fio)}</b>")
    author_text = "\n".join(author_lines)
    caption = (
        f"{author_text}\n"
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
        )



def find_local_invoice(directory: Path) -> Path | None:
    for suffix in ("pdf", "png", "jpg", "jpeg"):
        invoice_path = directory / f"invoice.{suffix}"
        if invoice_path.exists():
            return invoice_path
    for path in directory.glob("*_check.*"):
        if path.suffix.lower().lstrip(".") in {"pdf", "png", "jpg", "jpeg"}:
            return path
    return None


async def configure_admin_menu(bot: Bot, settings: Settings) -> None:
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except Exception as error:
        print(f"Default menu setup error: {error}")

    if not settings.webapp_base_url:
        return

    for admin_id in settings.admin_ids:
        await set_admin_menu_button(bot, admin_id, settings)


async def set_admin_menu_button(bot: Bot, admin_id: int, settings: Settings) -> None:
    menu_button = MenuButtonWebApp(
        text="Создать заявку",
        web_app=WebAppInfo(url=admin_web_app_url(settings)),
    )
    try:
        await bot.set_chat_menu_button(chat_id=admin_id, menu_button=menu_button)
    except Exception as error:
        print(f"Admin menu setup error for {admin_id}: {error}")


async def reset_user_menu_button(bot: Bot, user_id: int) -> None:
    try:
        await bot.set_chat_menu_button(chat_id=user_id, menu_button=MenuButtonDefault())
    except Exception as error:
        print(f"User menu reset error for {user_id}: {error}")


@router.message()
async def fallback(message: Message, settings: Settings) -> None:
    if message.from_user.id in settings.admin_ids:
        await send_admin_home(message, settings)
        return
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

    await configure_admin_menu(bot, settings)
    web_runner = None
    if settings.webapp_enabled:
        web_runner = await start_web_server(settings, db, bot)
    try:
        await dispatcher.start_polling(bot)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
