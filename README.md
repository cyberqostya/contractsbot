# BogrBot

Telegram-бот на `aiogram 3` для автоматизации договоров и счетов блогеров.

## Что делает

- Принимает сумму из подписанного deep-link, который генерирует админ.
- Спрашивает статус: `ИП` или `Самозанятый`.
- Пошагово собирает реквизиты в чате.
- Подставляет реквизиты и сумму в `.docx` шаблон из `wordtemplates/`.
- Конвертирует договор в PDF через LibreOffice.
- Ждет от блогера подписанный договор и счет (`PDF`, `PNG`, `JPG`, `JPEG`).
- Проверяет, что в тексте счета есть нужная сумма.
- Отправляет админу файлы с кнопкой `Оплачено`.
- После нажатия уведомляет блогера об успешной оплате.

## Установка

### WSL / Ubuntu

```bash
sudo apt update
sudo apt install python3.10-venv libreoffice tesseract-ocr tesseract-ocr-rus
python3 -m venv .venv-linux
source .venv-linux/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Если проект открыт в Windows, не используйте Windows-venv из `.venv/Scripts` внутри WSL. Для WSL используйте отдельную папку `.venv-linux`.

### Windows без WSL

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

На Windows отдельно установите LibreOffice и Tesseract OCR, затем добавьте их в `PATH`.

Заполните `.env`:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
RUK_NAME=Гунич И.И.
LINK_SECRET=change-this-random-secret
```

`ADMIN_IDS` можно указать через запятую: `123,456`.

Для конвертации DOCX в PDF нужен LibreOffice:

```bash
sudo apt install libreoffice
```

Для OCR картинок счетов нужен Tesseract:

```bash
sudo apt install tesseract-ocr tesseract-ocr-rus
```

## Шаблоны Word

Положите шаблоны в `wordtemplates/`:

- `sz_contract.docx` для самозанятого
- `ip_contract.docx` для ИП

Или поменяйте имена в `.env`.

В шаблонах используйте теги:

```text
{{ status_label }}
{{ amount }}
{{ contract_number }}
{{ year }}
{{ date }}
{{ ruk_name }}
{{ fio }}
{{ fio_initials }}
{{ inn }}
{{ passport_ser_num }}
{{ passport_series }}
{{ passport_number }}
{{ passport_issued_by }}
{{ passport_issue_date }}
{{ passport_department_code }}
{{ passport_registration }}
{{ bank_name }}
{{ bik }}
{{ correspondent_account }}
{{ checking_account }}
{{ email }}
{{ phone }}
{{ legal_address }}
{{ ogrn }}
{{ created_at }}
```

Для ИП часть паспортных тегов будет пустой, для СЗ пустыми будут `legal_address`, `ogrn`.
`{{ passport_ser_num }}` выводится в виде `1234 567890`; старые `{{ passport_series }}` и `{{ passport_number }}` тоже заполняются отдельно.

`{{ date }}` и `{{ passport_issue_date }}` выводятся в формате `«01» июня  2026 г.`. Дату выдачи паспорта пользователь может вводить как `01.06.2026`.

## Запуск

```bash
python3 -m bot
```

Для создания персональной ссылки отправьте боту от админа:

```text
/link 15000
```

Бот вернет ссылку вида:

```text
https://t.me/bogrcontractsbot?start=a_15000_xxxxxxxxxx
```

Без такой ссылки пользователь не сможет начать заполнение.
