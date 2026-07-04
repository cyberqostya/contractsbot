from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: set[int]
    ruk_name: str
    link_secret: str
    database_path: Path
    templates_dir: Path
    tztemplates_dir: Path
    output_dir: Path
    ip_template: str
    sz_template: str
    webapp_enabled: bool
    webapp_host: str
    webapp_port: int
    webapp_base_url: str
    webapp_admin_token: str
    telegram_proxy_url: str
    wbid: str


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set")

    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = {
        int(item.strip())
        for item in admin_ids_raw.split(",")
        if item.strip()
    }
    if not admin_ids:
        raise RuntimeError("ADMIN_IDS is not set")

    webapp_enabled_raw = os.getenv("WEBAPP_ENABLED", "1").strip().lower()

    return Settings(
        bot_token=bot_token,
        admin_ids=admin_ids,
        ruk_name=os.getenv("RUK_NAME", "Гунич И.И.").strip() or "Гунич И.И.",
        link_secret=os.getenv("LINK_SECRET", bot_token).strip() or bot_token,
        database_path=Path(os.getenv("DATABASE_PATH", "data/babycollab.sqlite3")),
        templates_dir=Path(os.getenv("TEMPLATES_DIR", "wordtemplates")),
        tztemplates_dir=Path(os.getenv("TZTEMPLATES_DIR", "tztemplates")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "generated")),
        ip_template=os.getenv("IP_TEMPLATE", "ip_contract.docx"),
        sz_template=os.getenv("SZ_TEMPLATE", "sz_contract.docx"),
        webapp_enabled=webapp_enabled_raw not in {"0", "false", "no", "off"},
        webapp_host=os.getenv("WEBAPP_HOST", "127.0.0.1").strip() or "127.0.0.1",
        webapp_port=int(os.getenv("WEBAPP_PORT", "8080")),
        webapp_base_url=os.getenv("WEBAPP_BASE_URL", "").strip().rstrip("/"),
        webapp_admin_token=os.getenv("WEBAPP_ADMIN_TOKEN", "").strip(),
        telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL", "").strip(),
        wbid=os.getenv("WBID", "10291").strip() or "10291",
    )
