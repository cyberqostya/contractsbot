from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import Bot
from aiogram.types import FSInputFile
from aiohttp import web

from bot.config import Settings
from bot.db import Database
from bot.documents import DocumentError, contract_filename, file_sha256, render_contract
from bot.forms import STATUS_IP, STATUS_SZ, fields_for_status, status_label


UTM_FIELDS = [
    ("utm_source", "tg"),
    ("utm_medium", "cpm"),
    ("utm_campaign", "stories"),
    ("utm_term", ""),
    ("utm_content", ""),
]


def list_tztemplate_files(settings: Settings) -> list[str]:
    if not settings.tztemplates_dir.exists():
        return []
    return sorted(
        path.name
        for path in settings.tztemplates_dir.iterdir()
        if path.is_file() and not path.name.startswith("~$")
    )


def split_links(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[\s,]+", value)
        if item.strip()
    ]


def add_utm_params(link: str, utm_values: dict[str, str]) -> str:
    split = urlsplit(link)
    query = [
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=True)
        if not key.startswith("utm_")
    ]
    query.extend((key, value) for key, value in utm_values.items() if value)
    return urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query), split.fragment)
    )


def format_amount(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits or html.escape(value)


def format_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return value.strip()


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
    if key == "passport_department_code":
        digits = re.sub(r"\D", "", value)
        return f"{digits[:3]}-{digits[3:]}"
    if key == "phone":
        return format_phone(value)
    if key == "email":
        return value.strip().lower()
    return re.sub(r"\s+", " ", value).strip()


def make_link_token() -> str:
    import secrets

    return secrets.token_urlsafe(18)


def link_is_active(personal_link, now) -> bool:
    return (
        personal_link is not None
        and personal_link.used_at is None
        and datetime.fromisoformat(personal_link.expires_at) >= now
    )


def now_moscow():
    from bot.app import now_moscow as app_now_moscow

    return app_now_moscow()


def iso_moscow(value):
    from bot.app import iso_moscow as app_iso_moscow

    return app_iso_moscow(value)


def validate_telegram_init_data(init_data: str, settings: Settings) -> int | None:
    user = parse_telegram_init_user(init_data, settings)
    if user is None:
        return None
    user_id = int(user.get("id", 0) or 0)
    if user_id not in settings.admin_ids:
        return None
    return user_id


def parse_telegram_init_user(init_data: str, settings: Settings) -> dict | None:
    if not init_data:
        return None

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    data = dict(pairs)
    received_hash = data.pop("hash", "")
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(
        b"WebAppData",
        settings.bot_token.encode(),
        hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    auth_date = int(data.get("auth_date", "0") or "0")
    if auth_date and time.time() - auth_date > 86400:
        return None

    try:
        user = json.loads(data.get("user", "{}"))
    except json.JSONDecodeError:
        return None

    user_id = int(user.get("id", 0) or 0)
    if not user_id:
        return None
    return user


def authenticate_admin(request: web.Request) -> int | str:
    settings: Settings = request.app["settings"]
    auth_header = request.headers.get("Authorization", "")
    if settings.webapp_admin_token and auth_header == f"Bearer {settings.webapp_admin_token}":
        return "token"

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user_id = validate_telegram_init_data(init_data, settings)
    if user_id is None:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "Недостаточно прав"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user_id


def json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


def admin_chat_id(authenticated_admin: int | str, settings: Settings) -> int:
    if isinstance(authenticated_admin, int):
        return authenticated_admin
    return sorted(settings.admin_ids)[0]


def link_summary_message(link: str, amount: str, template_files: list[str], product_links: list[str]) -> str:
    lines = [
        f"Ссылка на сумму <b>{format_amount(amount)} руб.</b>",
        link,
        "",
        "ТЗ:",
    ]
    if template_files:
        lines.extend(html.escape(name) for name in template_files)
    else:
        lines.append("не выбрано")

    lines.extend(["", "Ссылки на товары:"])
    if product_links:
        lines.extend(html.escape(item) for item in product_links)
    else:
        lines.append("не указаны")
    lines.extend(["", "Срок действия этой ссылки - 24 часа"])
    return "\n".join(lines)


async def admin_page(request: web.Request) -> web.FileResponse:
    response = web.FileResponse(Path(__file__).parent / "static" / "admin.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


async def user_form_page(request: web.Request) -> web.FileResponse:
    response = web.FileResponse(Path(__file__).parent / "static" / "form.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


async def admin_bootstrap(request: web.Request) -> web.Response:
    authenticate_admin(request)
    settings: Settings = request.app["settings"]
    return web.json_response(
        {
            "templates": list_tztemplate_files(settings),
            "utmFields": [{"key": key, "default": default} for key, default in UTM_FIELDS],
            "defaults": {
                "utm_source": "tg",
                "utm_medium": "cpm",
                "utm_campaign": "stories",
                "utm_term": "",
                "utm_content": "",
            },
            "wbid": settings.wbid,
        }
    )


async def create_admin_link(request: web.Request) -> web.Response:
    authenticate_admin(request)
    settings: Settings = request.app["settings"]
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return json_error("Некорректный JSON.")

    amount = re.sub(r"\D", "", str(payload.get("amount", "")))
    if not amount or int(amount) <= 0:
        return json_error("Введите сумму цифрами.")

    available_templates = set(list_tztemplate_files(settings))
    requested_templates_raw = payload.get("templateFiles", [])
    if not isinstance(requested_templates_raw, list):
        return json_error("Некорректный список ТЗ-файлов.")
    requested_templates = [str(item) for item in requested_templates_raw]
    selected_templates = [
        item
        for item in requested_templates
        if item in available_templates
    ]
    unknown_templates = [
        item
        for item in requested_templates
        if item not in available_templates
    ]
    if unknown_templates:
        return json_error("Один или несколько ТЗ-файлов не найдены.")

    product_links_raw = payload.get("productLinks", [])
    if isinstance(product_links_raw, str):
        product_links = split_links(product_links_raw)
    else:
        product_links = [str(item).strip() for item in product_links_raw if str(item).strip()]

    if not product_links:
        return json_error("Добавьте хотя бы одну ссылку на товар.")
    invalid_links = [link for link in product_links if not re.match(r"^https?://", link)]
    if invalid_links:
        return json_error("Каждая ссылка должна начинаться с http:// или https://.")

    ad_target = str(payload.get("adTarget", "instagram"))
    utm_values: dict[str, str] = {}
    if ad_target != "instagram":
        raw_utm = payload.get("utm", {})
        if not isinstance(raw_utm, dict):
            return json_error("Некорректные UTM-метки.")
        for key, _default in UTM_FIELDS:
            value = str(raw_utm.get(key, "")).strip()
            if value:
                utm_values[key] = value
        if not utm_values.get("utm_medium"):
            return json_error("Заполните utm_medium.")
        campaign_name = utm_values.get("utm_campaign", "")
        if not campaign_name:
            return json_error("Заполните utm_campaign.")
        utm_values["utm_campaign"] = f"{settings.wbid}-id-{campaign_name}"
        product_links = [add_utm_params(link, utm_values) for link in product_links]

    now = now_moscow()
    token = make_link_token()
    db.delete_expired_links(iso_moscow(now))
    db.create_personal_link(
        token=token,
        amount=amount,
        template_files=selected_templates,
        product_links=product_links,
        created_at=iso_moscow(now),
        expires_at=iso_moscow(now + timedelta(days=1)),
    )

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={token}"
    return web.json_response(
        {
            "link": link,
            "token": token,
            "amount": amount,
            "amountFormatted": f"{format_amount(amount)} руб.",
            "templateFiles": selected_templates,
            "productLinks": product_links,
            "expiresAt": iso_moscow(now + timedelta(days=1)),
        }
    )


async def send_admin_link(request: web.Request) -> web.Response:
    authenticated_admin = authenticate_admin(request)
    settings: Settings = request.app["settings"]
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return json_error("Некорректный JSON.")

    token = str(payload.get("token", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", token):
        return json_error("Некорректная ссылка.")

    personal_link = db.get_personal_link(token)
    if personal_link is None:
        return json_error("Ссылка не найдена.", status=404)

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={personal_link.token}"
    await bot.send_message(
        admin_chat_id(authenticated_admin, settings),
        link_summary_message(
            link,
            personal_link.amount,
            personal_link.template_files,
            personal_link.product_links,
        ),
        disable_web_page_preview=True,
    )
    return web.json_response({"ok": True})


def form_fields_payload(status: str) -> list[dict[str, str]]:
    return [
        {
            "key": field.key,
            "label": field.label,
            "example": field.example,
        }
        for field in fields_for_status(status)
    ]


def validate_form_requisites(status: str, values: dict) -> tuple[dict[str, str], dict[str, str]]:
    normalized: dict[str, str] = {}
    errors: dict[str, str] = {}
    for field in fields_for_status(status):
        raw_value = str(values.get(field.key, "")).strip()
        if not raw_value:
            errors[field.key] = "Заполните поле."
            continue
        validation_error = field.validate(raw_value)
        if validation_error:
            errors[field.key] = validation_error
            continue
        normalized[field.key] = normalize_field_value(field.key, raw_value)
    return normalized, errors


async def user_form_bootstrap(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    db: Database = request.app["db"]
    user = parse_telegram_init_user(request.headers.get("X-Telegram-Init-Data", ""), settings)
    if user is None:
        return json_error("Откройте форму из Telegram.", status=403)

    token = request.query.get("token", "").strip()
    personal_link = db.get_personal_link(token) if token else None
    now = now_moscow()
    db.delete_expired_links(iso_moscow(now))
    if not link_is_active(personal_link, now):
        return json_error("Ссылка уже использована или срок действия истек.", status=410)

    return web.json_response(
        {
            "amount": personal_link.amount,
            "amountFormatted": f"{format_amount(personal_link.amount)} руб.",
            "statuses": [
                {"value": STATUS_SZ, "label": status_label(STATUS_SZ)},
                {"value": STATUS_IP, "label": status_label(STATUS_IP)},
            ],
            "fields": {
                STATUS_SZ: form_fields_payload(STATUS_SZ),
                STATUS_IP: form_fields_payload(STATUS_IP),
            },
        }
    )


async def submit_user_form(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]
    user = parse_telegram_init_user(request.headers.get("X-Telegram-Init-Data", ""), settings)
    if user is None:
        return json_error("Откройте форму из Telegram.", status=403)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return json_error("Некорректный JSON.")

    token = str(payload.get("token", "")).strip()
    status = str(payload.get("status", STATUS_SZ)).strip()
    if status not in {STATUS_SZ, STATUS_IP}:
        return json_error("Выберите статус.")
    values = payload.get("requisites", {})
    if not isinstance(values, dict):
        return json_error("Некорректные данные формы.")

    requisites, errors = validate_form_requisites(status, values)
    if errors:
        return web.json_response({"errors": errors}, status=400)

    now = now_moscow()
    db.delete_expired_links(iso_moscow(now))
    personal_link = db.get_personal_link(token) if token else None
    if not link_is_active(personal_link, now):
        return json_error("Ссылка уже использована или срок действия истек.", status=410)

    user_id = int(user["id"])
    username = user.get("username")
    if not db.mark_personal_link_used(
        token=personal_link.token,
        user_id=user_id,
        application_id=None,
        used_at=iso_moscow(now),
    ):
        return json_error("Эта ссылка уже использована.", status=409)

    application_id = db.create_application(
        user_id=user_id,
        username=username,
        amount=personal_link.amount,
        status=status,
        requisites=requisites,
        generated_contract_path="",
        created_at=now.strftime("%H:%M %d.%m.%Y"),
    )
    db.attach_application_to_link(personal_link.token, application_id)

    try:
        pdf_path = await asyncio.to_thread(
            render_contract,
            settings=settings,
            application_id=application_id,
            amount=personal_link.amount,
            status=status,
            requisites=requisites,
        )
    except DocumentError as error:
        await bot.send_message(user_id, f"Не удалось подготовить договор: {error}")
        return json_error(f"Не удалось подготовить договор: {error}", status=500)

    db.update_generated_contract_hash(application_id, await asyncio.to_thread(file_sha256, pdf_path))
    db.set_status(application_id, "waiting_signed_contract")
    await bot.send_document(
        user_id,
        FSInputFile(pdf_path, filename=contract_filename(requisites.get("fio", ""))),
        caption=(
            "Договор готов.\n<b>Подпишите</b> его и отправьте сюда PDF-файл <b>подписанного</b> договора.\nБез подписанного договора оплата не произведётся."
        ),
    )
    shutil.rmtree(pdf_path.parent, ignore_errors=True)
    return web.json_response({"ok": True})


def create_web_app(settings: Settings, db: Database, bot: Bot) -> web.Application:
    app = web.Application()
    app["settings"] = settings
    app["db"] = db
    app["bot"] = bot
    app.router.add_get("/", admin_page)
    app.router.add_get("/admin", admin_page)
    app.router.add_get("/form", user_form_page)
    app.router.add_static("/static/", Path(__file__).parent / "static", name="static")
    app.router.add_get("/api/admin/bootstrap", admin_bootstrap)
    app.router.add_post("/api/admin/links", create_admin_link)
    app.router.add_post("/api/admin/links/send", send_admin_link)
    app.router.add_get("/api/form/bootstrap", user_form_bootstrap)
    app.router.add_post("/api/form/submit", submit_user_form)
    return app


async def start_web_server(settings: Settings, db: Database, bot: Bot) -> web.AppRunner:
    runner = web.AppRunner(create_web_app(settings, db, bot))
    await runner.setup()
    site = web.TCPSite(runner, settings.webapp_host, settings.webapp_port)
    await site.start()
    print(f"Admin web app: http://{settings.webapp_host}:{settings.webapp_port}/admin")
    return runner
