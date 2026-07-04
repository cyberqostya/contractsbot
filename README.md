# BabycollabBot

Telegram-бот на `aiogram 3` для автоматизации договоров и счетов блогеров.

## Что делает

- Принимает сумму из подписанного deep-link, который генерирует админ.
- Открывает веб-анкету для выбора статуса: `ИП` или `Самозанятый`.
- Собирает реквизиты в Telegram WebApp с валидациями.
- Подставляет реквизиты и сумму в `.docx` шаблон из `wordtemplates/`.
- Конвертирует договор в PDF через LibreOffice.
- Ждет от блогера подписанный договор и счет (`PDF`, `PNG`, `JPG`, `JPEG`).
- Проверяет, что в тексте счета есть нужная сумма.
- Отправляет админу подписанный договор и счет.

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
WEBAPP_BASE_URL=https://example.com
WEBAPP_ADMIN_TOKEN=change-this-local-admin-token
TELEGRAM_PROXY_URL=socks5://127.0.0.1:1080
WBID=10291
DATABASE_PATH=data/babycollab.sqlite3
```

`ADMIN_IDS` можно указать через запятую: `123,456`.
`WEBAPP_BASE_URL` нужен для кнопки меню Telegram WebApp. Это должен быть
публичный HTTPS-адрес, который проксирует локальный веб-сервер бота. Для локальной
проверки в обычном браузере можно открыть форму с `WEBAPP_ADMIN_TOKEN`.
`TELEGRAM_PROXY_URL` нужен только на серверах, где прямой доступ к `api.telegram.org`
закрыт. Если прокси не нужен, оставьте переменную пустой или удалите ее.

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
{{ status }}
{{ status_label }}
{{ amount }}
{{ contract_number }}
{{ year }}
{{ date }}
{{ monthyear }}
{{ ruk_name }}
{{ fio }}
{{ fio_initials }}
{{ nickname }}
{{ inn }}
{{ passport_ser_num }}
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
`{{ passport_ser_num }}` выводится в виде `1234 567890`.
`{{ passport_department_code }}` выводится в виде `000-000`.
Российский номер телефона выводится в виде `+7 (999) 999-99-99`.

`{{ date }}` и `{{ passport_issue_date }}` выводятся в формате `«01» июня  2026 г.`. Дату выдачи паспорта пользователь может вводить как `01.06.2026`.
`{{ monthyear }}` выводит месяц и год публикации видео, например `июль 2026 г.`.
Если договор формируется за 4 дня до конца месяца или позже, выводится текущий и
следующий месяц: `июль-август 2026 г.`. Для перехода года: `декабрь 2026-январь 2027 г.`.

## Запуск

```bash
python3 -m bot
```

По умолчанию вместе с ботом запускается админская веб-форма:

```text
http://127.0.0.1:8080/admin
```

Настройки веб-части:

```env
WEBAPP_ENABLED=1
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8080
WEBAPP_BASE_URL=https://example.com
WEBAPP_ADMIN_TOKEN=change-this-local-admin-token
TELEGRAM_PROXY_URL=socks5://127.0.0.1:1080
WBID=10291
```

В Telegram WebApp доступ проверяется по `initData` и `ADMIN_IDS`. Для локальной
отладки без Telegram откройте:

```text
http://127.0.0.1:8080/admin?token=change-this-local-admin-token
```

Когда бот запущен и `WEBAPP_BASE_URL` указан, для каждого админа из `ADMIN_IDS`
бот настраивает кнопку меню Telegram `Создать заявку`. Админ открывает чат с ботом,
нажимает кнопку меню или пишет `/start`, затем открывает веб-форму.

В форме можно заполнить:

1. сумма договора;
2. выбор файлов из папки `tztemplates`;
3. ссылка или ссылки на товары;
4. выбор `Instagram` или `Не Instagram`;
5. для `Не Instagram` UTM-метки.

Для UTM по умолчанию предлагаются:

```text
utm_source = tg
utm_medium = cpm
utm_campaign = stories
utm_term = пусто
utm_content = пусто
```

Чтобы не добавлять конкретную метку, оставьте поле пустым.
Для площадки `Не Instagram` поля `utm_medium` и `utm_campaign` обязательны.
Итоговое значение `utm_campaign` формируется как `WBID-id-значение`, например
`10291-id-summer_sale`.

Бот вернет ссылку вида:

```text
https://t.me/your_bot_username?start=xxxxxxxxxxxxxxxxxxxxxxxx
```

Сумма в ссылке больше не видна. Ссылка живет 24 часа. Открыть ее могут разные люди,
но после подтверждения анкеты первым пользователем она становится использованной,
и следующий пользователь получит сообщение, что ссылка уже использована.
После открытия персональной ссылки бот покажет кнопку веб-анкеты. Пользователь выбирает
`Самозанятый` или `ИП`, заполняет поля в веб-форме, после отправки формы бот пришлет
договор в чат. Выбранные файлы из `tztemplates` пользователь получит только после принятого счета и сообщения
`Документы переданы менеджеру`. Подготовленные ссылки на товары показываются админу
при создании персональной ссылки.

После получения подписанного договора и счета бот отправит документы админу.

## Локальные frontend-файлы

Формы не зависят от внешних CDN во время работы. Скрипты должны лежать локально:

```text
bot/static/vendor/telegram-web-app.js
bot/static/vendor/vue.global.prod.js
```

Если этих файлов нет на сервере, скачайте их один раз при деплое или перенесите
из локальной папки проекта.

## SQLite

Бот использует SQLite-файл `data/babycollab.sqlite3` по умолчанию. Это обычный файл
локальной базы данных, отдельный сервер для него не нужен.

В таблице `applications` хранятся заявки пользователей: Telegram ID, username,
сумма, статус ИП/самозанятый, реквизиты в JSON, file_id подписанного договора и
счета, текст счета и технический статус шага.

В таблице `personal_links` хранятся одноразовые ссылки: случайный токен, сумма,
список выбранных файлов, подготовленные ссылки на товары, время создания, время
истечения, отметка использования и ID заявки. При создании и открытии ссылок бот
удаляет истекшие записи, поэтому ссылки со сроком жизни 24 часа не будут
накапливаться бесконечно. Заявки в `applications` остаются как история работы.
