# AI PDF Signing Service

Локальный и Docker-ready сервис пакетной подписи PDF. Есть веб-интерфейс, авторизация по логину/паролю, загрузка пользовательской подписи и печати, AI/OCR-анализ, preview, ручная правка и экспорт PDF/ZIP.

## Локальный запуск

```bash
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python run.py
```

Healthcheck:

```bash
curl http://127.0.0.1:8000/health
```

Веб-интерфейс:

```text
http://127.0.0.1:8000/
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

## Интеграционный API

1. Зарегистрироваться или войти:

```bash
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","login":"user","password":"secret123","password_repeat":"secret123"}'

curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login":"user","password":"secret123"}'
```

2. Обработать PDF и получить JSON-отчет со ссылкой на скачивание:

```bash
curl -X POST http://127.0.0.1:8000/api/process \
  -H "Authorization: Bearer <access_token>" \
  -F "files=@document.pdf" \
  -F "use_ai=false"
```

Ответ содержит `download_url`, `files`, `jobs`, `warnings`. В `jobs` возвращается, что получилось: `signed`, `stamped`, `name_added`, `placements_count`, `needs_manual_review`, `warnings`, `errors`.

3. Сразу получить готовый PDF/ZIP:

```bash
curl -X POST http://127.0.0.1:8000/api/process-file \
  -H "Authorization: Bearer <access_token>" \
  -F "files=@document.pdf" \
  -F "use_ai=false" \
  -o signed.pdf
```

`/api/process-file` возвращает `application/pdf` для одного документа или `application/zip` для нескольких. Заголовок `X-Signing-Report` содержит base64url JSON-отчет.

## Docker Compose

Docker Compose по умолчанию:

- поднимает `postgres:17-alpine`;
- публикует backend на `http://127.0.0.1:8000`;
- публикует PostgreSQL на `127.0.0.1:5433`;
- включает авторизацию `AUTH_REQUIRED=true`;
- монтирует `/Users/rafailvv/Documents` в контейнер как `/app/assets`;
- использует подпись `/app/assets/подпись.png`;
- использует печать `/app/assets/Печать.png`;
- хранит runtime-файлы в Docker volume `signing_runtime`.
- хранит пользователей и PNG-ассеты в PostgreSQL volume `pg_data`.
- не хранит загруженные PDF в PostgreSQL.

Запуск:

```bash
docker compose up --build
```

Healthcheck:

```bash
curl http://127.0.0.1:8000/health
```

Остановка:

```bash
docker compose down
```

## Прод-развертывание

`docker-compose.prod.yml` рассчитан на сервер, где уже работает общий `nginxproxy/nginx-proxy` и letsencrypt companion в Docker-сети `web`.

Минимальный `.env` для production:

```env
SECRET_KEY=replace-with-long-random-secret
POSTGRES_DB=signing_documents
POSTGRES_USER=signing_documents
POSTGRES_PASSWORD=replace-with-strong-db-password
VIRTUAL_HOST=sign.innoprog.ru
LETSENCRYPT_HOST=sign.innoprog.ru
LETSENCRYPT_EMAIL=admin@innoprog.ru
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
ASSETS_DIR=/opt/signing-documents/assets
```

Запуск:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Контейнер приложения перед стартом выполняет:

```bash
alembic upgrade head
```

После запуска домен `sign.innoprog.ru` должен быть обслужен через внешнюю сеть `web`, `VIRTUAL_HOST`, `LETSENCRYPT_HOST` и общий letsencrypt companion.

## Переменные окружения

Скопируйте `.env.example` в `.env`, если нужно переопределить настройки:

```bash
cp .env.example .env
```

Основные переменные:

- `OPENAI_API_KEY` - ключ OpenAI API.
- `OPENAI_BASE_URL` - базовый URL API, по умолчанию `https://api.openai.com/v1`.
- `OPENAI_MODEL` - модель OpenAI, задается явно.
- `OPENAI_TIMEOUT_SECONDS` - таймаут AI-запроса.
- `SIGNATURE_IMAGE_PATH` - путь к PNG подписи.
- `STAMP_IMAGE_PATH` - путь к PNG печати.
- `OCR_LANGUAGES` - языки Tesseract OCR, по умолчанию `rus+eng`.
- `WORKDIR` - рабочая папка runtime-файлов.
- `AUTH_REQUIRED` - включает обязательную авторизацию для рабочих endpoints.
- `SECRET_KEY` - ключ подписи JWT.
- `ACCESS_TOKEN_EXPIRE_MINUTES` - срок жизни access token.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` - настройки PostgreSQL.
- `DATABASE_URL` - прямое переопределение URL БД, полезно для тестов или SQLite.
- `DOCKER_SIGNATURE_IMAGE_PATH`, `DOCKER_STAMP_IMAGE_PATH` - пути к дефолтным ассетам внутри Docker-контейнера.

## Тесты

```bash
./.venv/bin/python -m pytest -q
```

На текущем этапе тесты проверяют:

- настройки без обязательного OpenAI key;
- авторизацию `register/login/me`;
- защиту рабочих endpoints при включенном `AUTH_REQUIRED`;
- изоляцию документов между пользователями;
- загрузку пользовательских PNG подписи и печати;
- healthcheck;
- базовые модели данных;
- валидацию bbox;
- локальное runtime-хранилище;
- загрузку одного и нескольких PDF;
- отказ по не-PDF файлам без поломки всего пакета;
- список загруженных документов `/jobs`;
- preview PDF через `/preview/{job_id}`;
- отдачу PNG-страниц preview;
- обратимую конвертацию координат PDF points <-> preview pixels;
- ручное сохранение placements через `/placement/{job_id}`;
- локальный анализ PDF через `/analyze`;
- извлечение текстового слоя, слов, координат и векторных горизонтальных линий;
- OCR fallback через Tesseract для PDF без текстового слоя;
- перевод координат OCR-слов из пикселей рендера в PDF points;
- поиск горизонтальных линий по растру для сканированных PDF;
- fuzzy matching частых OCR-ошибок в якорях;
- поиск якорей `Венедиктов`, `Венедиктов Р.В.`, `Генеральный директор`, `подпись`, `ФИО`;
- создание `SignatureTarget` с confidence и warnings;
- автоматический расчет placements для подписи, печати и ФИО после `/analyze`;
- AI-анализ через OpenAI Responses API, если заданы `OPENAI_API_KEY` и `OPENAI_MODEL`;
- structured JSON validation для AI-решений;
- отправку в AI только подготовленного контекста: текст, слова, линии, кандидаты и crop-изображения зон;
- fallback на локальные placements при любой ошибке AI;
- отображение авторазмещений в preview перед экспортом;
- экспорт одного PDF через `/export` и `/download/{export_id}`;
- одношаговый API `/api/process` с JSON-отчетом и `download_url`;
- одношаговый API `/api/process-file`, который сразу возвращает PDF/ZIP;
- ZIP-экспорт нескольких PDF;
- вставку PNG подписи/печати и ФИО в итоговый PDF.
