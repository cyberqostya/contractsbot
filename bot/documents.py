from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

import fitz
import pytesseract
from docxtpl import DocxTemplate
from PIL import Image

from bot.config import Settings
from bot.forms import ALL_TEMPLATE_KEYS, STATUS_IP, status_label


class DocumentError(RuntimeError):
    pass


MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def render_contract(
    *,
    settings: Settings,
    application_id: int,
    amount: str,
    status: str,
    requisites: dict[str, str],
) -> Path:
    template_name = settings.ip_template if status == STATUS_IP else settings.sz_template
    template_path = settings.templates_dir / template_name
    if not template_path.exists():
        raise DocumentError(
            f"Не найден шаблон {template_path}. Положите .docx файл в папку wordtemplates."
        )

    output_dir = settings.output_dir / str(application_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_path = output_dir / "contract.docx"
    pdf_path = output_dir / "contract.pdf"

    today = date.today()
    context = {key: requisites.get(key, "") for key in ALL_TEMPLATE_KEYS}
    if requisites.get("passport_ser_num"):
        digits = re.sub(r"\D", "", requisites["passport_ser_num"])
        context["passport_ser_num"] = f"{digits[:4]} {digits[4:]}"
        context["passport_series"] = digits[:4]
        context["passport_number"] = digits[4:]
    context["passport_issue_date"] = format_user_date(
        requisites.get("passport_issue_date", "")
    )
    context.update(
        {
            "amount": amount,
            "contract_number": make_contract_number(today, application_id),
            "status": status,
            "status_label": status_label(status),
            "year": str(today.year),
            "date": format_document_date(today),
            "ruk_name": settings.ruk_name,
            "fio_initials": make_fio_initials(requisites.get("fio", "")),
            "created_at": datetime.now().strftime("%d.%m.%Y"),
        }
    )

    template = DocxTemplate(template_path)
    template.render(context)
    template.save(docx_path)
    convert_docx_to_pdf(docx_path, output_dir)

    if not pdf_path.exists():
        raise DocumentError("LibreOffice не создал PDF файл договора.")
    return pdf_path


def make_contract_number(current_date: date, application_id: int) -> str:
    seed = current_date.year * 10000 + current_date.month * 100 + current_date.day
    number = ((seed * 37) + (application_id * 7919)) % 90000 + 10000
    return str(number)


def format_document_date(value: date) -> str:
    month = MONTHS_GENITIVE[value.month]
    return f"«{value.day:02d}» {month}  {value.year} г."


def format_user_date(value: str) -> str:
    parsed = parse_user_date(value)
    if parsed is None:
        return value
    return format_document_date(parsed)


def parse_user_date(value: str) -> date | None:
    value = value.strip()
    for pattern in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue

    match = re.fullmatch(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", value.lower())
    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2)
    year = int(match.group(3))
    for month_number, genitive_name in MONTHS_GENITIVE.items():
        if month_name == genitive_name:
            try:
                return date(year, month_number, day)
            except ValueError:
                return None
    return None


def make_fio_initials(fio: str) -> str:
    parts = [part for part in re.split(r"\s+", fio.strip()) if part]
    if not parts:
        return ""
    surname = parts[0]
    initials = "".join(f"{part[0].upper()}." for part in parts[1:] if part)
    return f"{surname} {initials}".strip()


TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def contract_filename(fio: str) -> str:
    surname = re.split(r"\s+", fio.strip())[0] if fio.strip() else "contract"
    latin = "".join(TRANSLIT.get(char.lower(), char.lower()) for char in surname)
    latin = re.sub(r"[^a-z0-9]+", "_", latin).strip("_")
    if latin:
        latin = latin[:1].upper() + latin[1:]
    return f"{latin or 'Contract'}_contract.pdf"


def convert_docx_to_pdf(docx_path: Path, output_dir: Path) -> None:
    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        raise DocumentError("LibreOffice не найден. Установите libreoffice для конвертации в PDF.")

    result = subprocess.run(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(docx_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise DocumentError(f"Ошибка конвертации DOCX в PDF: {result.stderr or result.stdout}")


def extract_text_from_invoice(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return extract_text_from_pdf(file_path)
    if suffix in {".png", ".jpg", ".jpeg"}:
        return ocr_image(file_path)
    raise DocumentError("Счет должен быть PDF, PNG, JPG или JPEG.")


def extract_text_from_pdf(file_path: Path) -> str:
    text_parts: list[str] = []
    with fitz.open(file_path) as document:
        for page in document:
            text = page.get_text("text").strip()
            if text:
                text_parts.append(text)
    text = "\n".join(text_parts)
    if text.strip():
        return text

    # Fallback for scanned PDFs.
    with fitz.open(file_path) as document:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            text_parts.append(pytesseract.image_to_string(image, lang="rus+eng"))
    return "\n".join(text_parts)


def ocr_image(file_path: Path) -> str:
    with Image.open(file_path) as image:
        return pytesseract.image_to_string(image, lang="rus+eng")


def amount_found_near_total(text: str, expected_amount: str) -> bool:
    expected = normalize_money(expected_amount)
    if not expected:
        return False

    normalized_text = re.sub(r"\s+", " ", text.lower())
    for total_match in re.finditer(r"\bитого\b", normalized_text):
        start = max(0, total_match.start() - 20)
        end = min(len(normalized_text), total_match.end() + 100)
        nearby = normalized_text[start:end]
        for amount_match in re.finditer(r"\d[\d\s.,]*", nearby):
            candidate = normalize_money(amount_match.group(0))
            if candidate == expected:
                return True
    return False


def signed_contract_has_marks(file_path: Path) -> bool:
    with fitz.open(file_path) as document:
        for page in document:
            annotations = page.annots()
            if annotations is not None and any(True for _ in annotations):
                return True
            if page.get_images(full=True):
                return True
            drawings = page.get_drawings()
            if len(drawings) >= 2:
                return True
    return False


def normalize_money(value: str) -> str:
    cleaned = re.sub(r"[^\d,\.]", "", value)
    if not cleaned:
        return ""

    if "," in cleaned or "." in cleaned:
        separator_index = max(cleaned.rfind(","), cleaned.rfind("."))
        integer = re.sub(r"\D", "", cleaned[:separator_index])
        fraction = re.sub(r"\D", "", cleaned[separator_index + 1 :])
        if fraction and int(fraction[:2].ljust(2, "0")) != 0:
            return f"{int(integer or '0')}.{fraction[:2].ljust(2, '0')}"
        return str(int(integer or "0"))

    return str(int(re.sub(r"\D", "", cleaned) or "0"))
