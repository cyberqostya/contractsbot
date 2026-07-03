from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from datetime import datetime
import re


STATUS_IP = "ip"
STATUS_SZ = "sz"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    example: str = ""
    validator: Callable[[str], str | None] | None = None

    def validate(self, value: str) -> str | None:
        if self.validator is None:
            return None
        return self.validator(value)


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def validate_digits(label: str, length: int) -> Callable[[str], str | None]:
    def validator(value: str) -> str | None:
        digits = only_digits(value)
        if len(digits) != length:
            return f"{label} должен содержать {length} цифр."
        return None

    return validator


def validate_fio(value: str) -> str | None:
    if "." in value or any(char.isdigit() for char in value):
        return "Введите ФИО полностью, без инициалов, точек и цифр."
    parts = [part for part in re.split(r"\s+", value.strip()) if part]
    if len(parts) < 3:
        return "Введите фамилию, имя и отчество полностью."
    for part in parts:
        normalized = part.replace("-", "")
        if len(normalized) < 2 or not normalized.isalpha():
            return "Каждая часть ФИО должна состоять из букв, минимум 2 буквы."
    return None


def validate_passport_ser_num(value: str) -> str | None:
    digits = only_digits(value)
    if len(digits) != 10:
        return "Серия и номер паспорта должны содержать 10 цифр: 4 цифры серии и 6 цифр номера."
    return None


def validate_email(value: str) -> str | None:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()):
        return "Введите корректный email."
    return None


def validate_phone(value: str) -> str | None:
    if not re.fullmatch(r"[\d\s()+-]+", value.strip()):
        return "Телефон может содержать цифры, +, пробелы, скобки и дефисы."
    if len(only_digits(value)) < 7:
        return "В телефоне должно быть минимум 7 цифр."
    return None


def validate_date_ddmmyyyy(value: str) -> str | None:
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value.strip()):
        return "Введите дату в формате ДД.ММ.ГГГГ."
    try:
        datetime.strptime(value.strip(), "%d.%m.%Y")
    except ValueError:
        return "Введите существующую календарную дату."
    return None


def validate_passport_department_code(value: str) -> str | None:
    digits = only_digits(value)
    if len(digits) != 6:
        return "Код подразделения должен содержать 6 цифр."
    return None


COMMON_FIELDS = [
    Field("fio", "ФИО полностью", "Иванов Иван Иванович", validate_fio),
    Field("nickname", "Никнейм", "nick123"),
    Field("inn", "ИНН", "123456789012", validate_digits("ИНН", 12)),
]


COMMON_REQUISITE_FIELDS = [
    Field("bank_name", "Название банка", "Тинькофф Банк"),
    Field("checking_account", "р/с (20 цифр)", "40800000000000000000", validate_digits("р/с", 20)),
    Field("correspondent_account", "к/с (20 цифр)", "30101000000000000000", validate_digits("к/с", 20)),
    Field("bik", "БИК (9 цифр)", "000000000", validate_digits("БИК", 9)),
    Field("email", "Email", "ivanov@mail.ru", validate_email),
    Field("phone", "Телефон", "89991234567", validate_phone),
]


SZ_FIELDS = [
    Field("passport_ser_num", "Серия и номер паспорта", "1234 567890", validate_passport_ser_num),
    Field("passport_issued_by", "Кем выдан паспорт", "ГУ МВД России по г. Москве"),
    Field("passport_issue_date", "Дата выдачи паспорта", "01.06.2026", validate_date_ddmmyyyy),
    Field("passport_department_code", "Код подразделения по паспорту", "770-001", validate_passport_department_code),
    Field("passport_registration", "Адрес регистрации по паспорту", "г. Москва, ул. Примерная, д. 1, кв. 1"),
]

IP_FIELDS = [
    Field("ogrn", "ОГРН (15 цифр)", "123456789012345", validate_digits("ОГРН", 15)),
    Field("legal_address", "Юридический адрес", "125009, г. Москва, ул. Примерная, д. 1"),
]


ALL_TEMPLATE_KEYS = {
    "fio",
    "nickname",
    "passport_ser_num",
    "passport_issued_by",
    "passport_issue_date",
    "passport_department_code",
    "passport_registration",
    "bank_name",
    "bik",
    "correspondent_account",
    "checking_account",
    "email",
    "phone",
    "legal_address",
    "inn",
    "ogrn",
}


def fields_for_status(status: str) -> list[Field]:
    if status == STATUS_IP:
        return [*COMMON_FIELDS, *IP_FIELDS, *COMMON_REQUISITE_FIELDS]
    if status == STATUS_SZ:
        return [*COMMON_FIELDS, *SZ_FIELDS, *COMMON_REQUISITE_FIELDS]
    raise ValueError(f"Unknown status: {status}")


def status_label(status: str) -> str:
    return "ИП" if status == STATUS_IP else "Самозанятый"
