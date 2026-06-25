# Price Watcher

Telegram-сервис для мониторинга цен на маркетплейсах **Wildberries**, **Ozon** и **Яндекс Маркет**.

Проект объединяет два сценария:

1. **Личный трекер цен** — пользователь добавляет товар и получает уведомление, когда цена падает до желаемой.
2. **Deal Channel Bot** — автоматический обход категорий, сбор истории цен и публикация скидок в Telegram-канал с умной оценкой выгодности.

---

## Возможности Deal Channel Bot

- Обход настроенных категорий на WB / Ozon / Яндекс Маркет по расписанию
- Парсинг карточек: название, цена, старая цена, фото, ссылка
- **История цен** — все карточки сохраняются в БД при каждом обходе
- **Двухуровневая оценка скидки:**
  - первая неделя (прогрев) — по данным парсера маркетплейса
  - после прогрева — по сравнению с **средней ценой за 90 дней** из БД
- **Модерация админом** — спорные скидки отправляются в ЛС с кнопками «Принять» / «Отклонить»
- Дедупликация — один товар публикуется в канал только один раз
- Один канал, категории различаются хештегами (`#beauty`, `#electronics` и т.д.)

### Пример поста в канале

```
🔥 Скидка 25% | Ozon

Крем для лица Nivea, 50 мл
1 890 ₽ → 1 229 ₽

#beauty #ozon #скидки
[ссылка на товар + фото]
```

Если скидка подтверждена только по средней цене из БД, пост помечается:

```
📊 Скидка относительно средней цены за 90 дней: 22%
```

---

## Архитектура

```
config/monitored_categories.yaml   # категории и URL для обхода
        │
        ▼
src/crawlers/                      # сбор product_id из категорий
        │
        ▼
src/parsers/                       # парсинг карточки товара
        │
        ▼
src/services/deal_pipeline.py      # crawl → parse → save prices → evaluate → post
        │
        ├── src/crud/price_tracking.py      # TrackedProduct + ProductPriceHistory
        ├── src/services/discount_evaluator.py
        ├── src/crud/deal_moderation.py     # журнал решений
        └── src/crud/posted_deal.py         # дедупликация публикаций
        │
        ▼
bot/deals_scheduler.py             # APScheduler, запуск каждые N минут
```

### Основные модели БД

| Модель | Назначение |
|--------|------------|
| `TrackedProduct` | Карточка товара с маркетплейса |
| `ProductPriceHistory` | Снимок цены при каждом обходе |
| `PostedDeal` | Уже опубликованные в канал скидки |
| `DealModeration` | Авто-пост, пропуск, ожидание, одобрено/отклонено |
| `Track` / `PriceHistory` | Legacy: личное отслеживание пользователем |

---

## Логика скидок

### Период прогрева (`DATA_COLLECTION_WARMUP_DAYS`, по умолчанию 7 дней)

Пока в БД накоплено меньше недели истории — решение принимается **только по скидке парсера** (`MIN_PARSER_DISCOUNT_PERCENT`).

### После прогрева

| Скидка по БД | Скидка по парсеру | Действие |
|--------------|-------------------|----------|
| ≥ порога (20%) | ≥ порога (15%) | Автопост в канал |
| ≥ порога | < порога | Автопост с пометкой «скидка по средней цене» |
| < порога | ≥ порога | Карточка админу на модерацию |
| < порога | < порога | Не публикуем, пишем в журнал |

Старые записи цен удаляются автоматически (старше `PRICE_HISTORY_RETENTION_DAYS`).

---

## Быстрый старт (Docker)

```bash
git clone https://github.com/Dushe4ka/price_watcher.git
cd price_watcher
cp .env.example .env
# заполните .env (токен бота, ID канала, пароли БД)
docker compose up -d --build
```

Миграции применяются автоматически при старте контейнера `api`.

- API: http://localhost/docs
- Бот: polling, scheduler стартует при запуске контейнера `telegram_bot`

### Остановка

```bash
docker compose down
```

---

## Переменные окружения

Скопируйте `.env.example` → `.env` и заполните значения.

### База данных и приложение

| Переменная | Описание |
|------------|----------|
| `DB_DIALECT` | `postgresql` |
| `DB_DRIVER` | `asyncpg` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | учётные данные PostgreSQL |
| `POSTGRES_HOST` | `db` в Docker |
| `SECRET` | секрет приложения |
| `JWT_SECRET_KEY` | ключ для JWT (32 байта, base64) |
| `FIRST_SUPERUSER_EMAIL` / `FIRST_SUPERUSER_PASSWORD` | первый админ API |

### Telegram

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | токен от [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHANNEL_ID` | ID канала для автопостинга |
| `ADMIN_TELEGRAM_ID` | Telegram ID админа для модерации |

### Deal Channel Bot

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEALS_ENABLED` | `true` | включить scheduler обхода |
| `CRAWL_INTERVAL_MINUTES` | `30` | интервал обхода |
| `MIN_DISCOUNT_PERCENT` | `15` | legacy-алиас для парсера |
| `MIN_PARSER_DISCOUNT_PERCENT` | `15` | порог скидки по данным парсера |
| `MIN_DATABASE_DISCOUNT_PERCENT` | `20` | порог скидки относительно средней из БД |
| `PRICE_HISTORY_RETENTION_DAYS` | `90` | срок хранения истории цен |
| `DATA_COLLECTION_WARMUP_DAYS` | `7` | период прогрева (только парсер) |
| `MAX_PRODUCTS_PER_CATEGORY` | `20` | лимит товаров за один обход категории |
| `CATEGORIES_CONFIG_PATH` | `config/monitored_categories.yaml` | файл категорий |
| `PROXY_LIST` | — | прокси через запятую (опционально) |

> Админ должен хотя бы раз написать боту `/start`, иначе бот не сможет отправить карточки на модерацию в ЛС.

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Запуск бота |
| `/menu` | Главное меню |
| `/deals_status` | Статус автопостинга, пороги, последние публикации |
| `/force_crawl` | Ручной запуск обхода категорий |

Также доступны команды личного трекера: `/auth`, `/account_settings` и др.

---

## Настройка категорий

Файл `config/monitored_categories.yaml`:

```yaml
categories:
  - slug: beauty
    hashtag: beauty
    name: Бьюти
    min_discount_percent: 15
    marketplaces:
      - marketplace: ozon
        crawl_url: /category/krasota-i-zdorove-6500/
      - marketplace: wildberries
        crawl_url: https://www.wildberries.ru/catalog/krasota
      - marketplace: yandex_market
        crawl_url: https://market.yandex.ru/catalog--krasota/54734/list
```

Поддерживаемые маркетплейсы: `ozon`, `wildberries`, `yandex_market`.

---

## Локальный запуск (без Docker)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn src.main:app --reload
python -m bot.main
```

---

## API

- `GET /docs` — Swagger UI
- `GET /deals/stats` — статистика опубликованных сделок
- `POST /deals/crawl` — ручной запуск pipeline (требует авторизации)

---

## Миграции

```bash
alembic upgrade head
alembic revision --autogenerate -m "описание"
```

Ключевые миграции:

- `a1b2c3d4e5f6` — таблица `posteddeal`
- `b2c3d4e5f6a7` — история цен и модерация
- `c3d4e5f6a7b8` — исправление `TIMESTAMPTZ` для дат

---

## Известные ограничения

- **Ozon** может отдавать 403 с IP дата-центра / Docker
- **Wildberries** — возможен rate limit (429), есть retry и задержки
- **Яндекс Маркет** — не всегда есть `highPrice` в JSON-LD
- Для стабильной работы Ozon/YM может потребоваться прокси (`PROXY_LIST`)

---

## Стек

FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL · python-telegram-bot · APScheduler · httpx · selectolax · PyYAML · Docker

---

## Авторы

**Походяев Константин** — оригинальный проект  
Telegram: [@kspohodyaev](https://t.me/kspohodyaev)

Расширение Deal Channel Bot: история цен, умная оценка скидок, модерация.
