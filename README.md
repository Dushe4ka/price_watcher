# Price Watcher / LuluSaleBot

Telegram-сервис для мониторинга цен на маркетплейсах **Wildberries**, **Ozon** и **Яндекс Маркет**.

Два сценария в одном боте:

1. **Личный трекер цен** — пользователь добавляет товар и получает уведомление, когда цена падает до желаемой.
2. **Deal Channel Bot** — автоматический обход категорий, сбор истории цен и публикация скидок в Telegram-канал.

---

## Возможности

### Личный трекер

- Регистрация и вход через бота (`/auth`)
- Добавление товара: кнопка, команда `/add` или **ссылка/артикул** прямо в чат
- Список отслеживаемых товаров, история цен, изменение целевой цены
- Периодические уведомления при снижении цены
- Удобная навигация: главное меню, справка, кнопки «Назад»

### Deal Channel Bot

- Обход 8 категорий (бьюти, дом, дети, электроника, одежда, обувь, аксессуары, мебель)
- Парсинг карточек: название, цена, старая цена, фото, ссылка, рейтинг, отзывы
- **Фильтр по рейтингу** — товары с оценкой ниже порога (`MIN_PRODUCT_RATING`) отсеиваются
- **История цен** — снимок при каждом обходе, хранение 90 дней
- **Двухуровневая оценка скидки** (парсер + средняя из БД)
- **Проверка по рынку** для дорогих товаров (от 10 000 ₽ в выбранных категориях)
- **Модерация** — спорные скидки админу в ЛС (кнопки «Принять» / «Отклонить» + «Перейти к товару»)
- **Публикация** — пост с фото, ценой, скидкой и кнопкой «🛒 Перейти к товару»
- Дедупликация — один товар в канал публикуется один раз
- **Отчёт обхода** — статистика с разбивкой по площадкам (WB / Ozon / YM)
- Хештеги категорий: `#beauty`, `#electronics` и т.д.
- **Фоновый обход** — `/force_crawl` не блокирует бота, кнопки работают во время обхода

---

## Текущий статус парсинга (важно)

| Площадка | Краулер | Парсер | Рейтинг | Статус |
|----------|---------|--------|---------|--------|
| **Wildberries** | ✅ v18 search API | ✅ basket CDN + search | ✅ | Основной источник |
| **Яндекс Маркет** | ⚠️ HTML-регулярки | ❌ SPA, нет JSON-LD | ❌ | Нужен headless-браузер |
| **Ozon** | ✅ Playwright + entrypoint-api | ✅ widgetStates / tiles | ✅ | Нужен чистый RU egress / `PROXY_LIST` |

**WB** — краулер использует поисковые запросы (`search_queries`) из конфига категорий и извлекает цены, рейтинг и отзывы прямо из ответа API. Парсер одиночных товаров получает базовую информацию через basket CDN и дополняет ценами/рейтингом через search API.

**Ozon** — headless Chromium греет antibot-сессию (`abt_att`), затем читает `entrypoint-api.bx` / `composer-api.bx`. При IP в антибот-бане (VPN/DC) — 403 и страница «нет соединения»; задайте `PROXY_LIST` (RU residential/mobile) и при необходимости `OZON_PROXY_REQUIRED=true`. Лимиты: `OZON_REQUEST_DELAY_SEC`, `OZON_FETCH_RETRIES`, circuit-breaker `OZON_BLOCK_COOLDOWN_SEC`. Smoke: `python -m scripts.smoke_ozon_crawl`.

**Почему скидок может быть 0:** скидка по истории БД появляется после нескольких дней обходов (`DATA_COLLECTION_WARMUP_DAYS`).

**Что помогает:** прокси/модем (`PROXY_LIST`), `DATA_COLLECTION_WARMUP_DAYS=3` на период накопления истории.

---

## Архитектура

```
config/monitored_categories.yaml
        │
        ▼
src/crawlers/                    # product_id из категорий
        │
        ▼
src/parsers/                     # карточка товара
        │
        ▼
src/services/deal_pipeline.py    # crawl → parse → save → evaluate → post
        ├── discount_evaluator.py
        ├── market_price_checker.py
        ├── price_tracking (CRUD)
        ├── deal_moderation (CRUD)
        └── posted_deal (CRUD)
        │
        ▼
bot/deals_scheduler.py           # APScheduler, каждые N минут

bot/navigation/                  # тексты, клавиатуры, навигация бота
bot/handlers/                    # команды и callback-обработчики
```

### Модели БД

| Модель | Назначение |
|--------|------------|
| `TrackedProduct` | Товар с маркетплейса (канал скидок) |
| `ProductPriceHistory` | Снимок цены при обходе |
| `PostedDeal` | Опубликованные в канал сделки |
| `DealModeration` | Журнал: пост / пропуск / модерация |
| `Track` / `PriceHistory` | Личное отслеживание пользователем |

---

## Логика скидок

### Прогрев (`DATA_COLLECTION_WARMUP_DAYS`)

Пока в БД нет истории **или** прошло меньше N дней с первой записи — решение только по **скидке парсера** (`MIN_PARSER_DISCOUNT_PERCENT`).

При `DATA_COLLECTION_WARMUP_DAYS=0` прогрев фактически только на самом первом запуске (пустая история).

### После прогрева

| Скидка по БД (20%) | Скидка парсера (15%) | Действие |
|--------------------|----------------------|----------|
| ✅ | ✅ | Автопост в канал |
| ✅ | ❌ | Пост с пометкой «скидка по средней цене» |
| ❌ | ✅ | На модерацию админу |
| ❌ | ❌ | Пропуск (журнал) |

Для товаров **≥ `MARKET_CHECK_MIN_PRICE`** в категориях из `MARKET_CHECK_CATEGORIES` дополнительно проверяется цена на других площадках.

---

## Быстрый старт (Docker)

```bash
git clone https://github.com/Dushe4ka/price_watcher.git
cd price_watcher
cp .env.example .env
# заполните .env
docker compose up -d --build
```

- API + Swagger: http://localhost:8000/docs  
- Миграции применяются при старте `api`  
- Бот: polling, scheduler в контейнере `telegram_bot`

```bash
docker compose down      # остановка
docker compose logs -f telegram_bot   # логи бота
```

---

## Переменные окружения

Скопируйте `.env.example` → `.env`.

### Telegram

| Переменная | Описание |
|------------|----------|
| `TELEGRAM_BOT_TOKEN` | токен [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHANNEL_ID` | ID канала для автопостинга |
| `ADMIN_TELEGRAM_ID` | ID админов через запятую: `123,456` |

### Deal Channel Bot

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DEALS_ENABLED` | `true` | scheduler обхода |
| `CRAWL_INTERVAL_MINUTES` | `30` | интервал обхода |
| `MIN_PARSER_DISCOUNT_PERCENT` | `15` | порог скидки с сайта |
| `MIN_DATABASE_DISCOUNT_PERCENT` | `20` | порог vs средняя из БД |
| `DATA_COLLECTION_WARMUP_DAYS` | `7` | дни прогрева (только парсер) |
| `PRICE_HISTORY_RETENTION_DAYS` | `90` | хранение истории |
| `MAX_PRODUCTS_PER_CATEGORY` | `20` | лимит товаров на МП за обход |
| `MARKET_CHECK_MIN_PRICE` | `10000` | порог проверки по рынку (₽) |
| `MARKET_CHECK_CATEGORIES` | `electronics,furniture,home` | категории для рыночной проверки |
| `MIN_PRODUCT_RATING` | `4.5` | минимальный рейтинг товара (0 = не фильтровать) |
| `PROXY_LIST` | — | прокси через запятую (для Ozon — RU residential/mobile) |
| `OZON_ENABLED` | `true` | включить Ozon crawl/parse |
| `OZON_PROXY_REQUIRED` | `false` | не ходить в Ozon без `PROXY_LIST` |
| `OZON_REQUEST_DELAY_SEC` | `0.5` | пауза между запросами Ozon |
| `OZON_FETCH_RETRIES` | `3` | ретраи + ротация прокси при 403 |
| `OZON_BLOCK_COOLDOWN_SEC` | `120` | cooldown после серии antibot-блоков |
| `CATEGORIES_CONFIG_PATH` | `config/monitored_categories.yaml` | категории |

> Админ должен написать боту `/start`, иначе модерация в ЛС не дойдёт.

---

## Команды бота

### Для всех

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие, вход в бота |
| `/menu` | Главное меню |
| `/help` | Как пользоваться |
| `/auth` | Войти в аккаунт |
| `/add` | Добавить товар (ссылка или артикул) |
| `/account_settings` | Аккаунт и профиль |

### Только админы

| Команда / кнопка | Описание |
|------------------|----------|
| `/deals_status` | Статус канала, пороги, последние посты |
| `/force_crawl` | Ручной обход категорий |
| **🛠 Админ-панель** в меню | То же через кнопки |

### Добавление товара (трекер)

1. Кнопка **«Добавить товар»** или `/add`
2. Отправить **ссылку** (WB / Ozon / ЯМ) или **артикул WB** (цифры)
3. Указать **желаемую цену**

Можно сразу кинуть ссылку на товар в чат (без кнопки) — бот распознает URL.

---

## Настройка категорий

`config/monitored_categories.yaml` — 8 категорий × 3 маркетплейса.

```yaml
categories:
  - slug: beauty
    hashtag: beauty
    name: Бьюти
    marketplaces:
      - marketplace: yandex_market
        crawl_url: https://market.yandex.ru/catalog--krasota/54734/list
      - marketplace: wildberries
        crawl_url: https://www.wildberries.ru/catalog/krasota
        search_queries:
          - "крем для лица"
          - "тушь для ресниц"
      - marketplace: ozon
        crawl_url: /category/krasota-i-zdorove-6500/
```

> **WB** использует `search_queries` для поиска через v18 API. `crawl_url` выступает как fallback-идентификатор категории.

---

## API

- `GET /docs` — Swagger
- `GET /deals/stats` — статистика сделок
- `POST /deals/crawl` — ручной pipeline (с авторизацией)

---

## Миграции

```bash
alembic upgrade head
```

| Ревизия | Содержание |
|---------|------------|
| `a1b2c3d4e5f6` | `posteddeal` |
| `b2c3d4e5f6a7` | история цен, модерация |
| `c3d4e5f6a7b8` | fix `TIMESTAMPTZ` |
| `d4e5f6a7b8c9` | поля market check в модерации |

---

## Структура бота

```
bot/
├── main.py
├── deals_scheduler.py
├── navigation/          # copy.py, keyboards.py, state.py, handlers.py
├── services/
│   └── track_link_parser.py   # разбор ссылок и артикулов
└── handlers/
    ├── base.py          # start, menu, help, админ-панель
    ├── track.py         # товары, quick-add
    ├── user.py          # аккаунт, auth
    ├── deals_admin.py   # статус и обход
    └── deal_moderation.py
```

---

## Стек

FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL · python-telegram-bot 22 · APScheduler · httpx · selectolax · Docker

---

## Авторы

**Походяев Константин** — оригинальный проект · [@kspohodyaev](https://t.me/kspohodyaev)

Расширения: Deal Channel Bot, история цен, модерация, навигация бота, проверка по рынку.
