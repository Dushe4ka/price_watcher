# План реализации: Price Watcher / LuluSaleBot

## Финишная картина

**Один Telegram-канал** с автопостингом скидок. Категории — хештегами. Личный трекер цен — в том же боте.

---

## Выполнено

### Фаза 0 — Инфраструктура ✅
- Docker Compose: `db`, `api`, `telegram_bot`, `nginx`
- Settings, `.env.example`, миграции Alembic

### Baseline репозитория и Docker ✅
- `.env` и варианты `.env.*` исключены из Git и Docker build context;
  отслеживается только безопасный `.env.example`.
- `Dockerfile.api` не копирует environment-файлы в image layers. Runtime
  получает конфигурацию через окружение контейнера.
- `python -m scripts.repository_hygiene` проверяет tracked secrets/runtime
  artifacts, Dockerfile и `.dockerignore` без чтения `.env`.

### Фаза 1 — Парсеры карточек ✅
- `src/parsers/`: WB, Ozon, Yandex Market
- `ParsedProduct`: цена, original_price, discount_percent

### Фаза 2 — Краулеры категорий ✅ (Ozon — код готов, ждёт рабочий прокси)
- `src/crawlers/` + `config/monitored_categories.yaml` (8 категорий × 3 МП)
- **Wildberries** — Playwright, headed-режим (Xvfb в проде), чтение прямо из DOM карточек. `search.wb.ru` теперь требует proof-of-work антибот-токен на каждый запрос — обычный HTTP больше не работает ни с одним IP, поэтому текстовый поиск (`search_queries`) убран, обход теперь по реальным URL категорий. См. `src/wb/`.
- **Яндекс Маркет** — JSON-LD `ItemList` + постраничный обход, обычный `httpx`, без браузера. URL-схема `/category/<slug>` (числовые ID `/catalog--slug/<id>/list` со временем «уезжают» на другие разделы).
- **Ozon** — Playwright + entrypoint-api, код рабочий, но **функционально заблокирован**: без резидентного/мобильного IP антибот блокирует полностью; протестированный шэйред мобильный прокси тоже не прошёл (3/3 неудачи, вероятно пул уже в стоп-листах Ozon). Нужен приватный/выделенный мобильный IP.

### Фаза 3 — Pipeline ✅
- `deal_pipeline.py`, `post_formatter.py`, `posted_deal`

### Фаза 4 — Bot + Scheduler ✅
- `deals_scheduler.py`, `/deals_status`, `/force_crawl`
- Модерация: `deal_moderation.py`

### Фаза 5 — История цен и умные скидки ✅
- `TrackedProduct`, `ProductPriceHistory`, `DealModeration`
- `discount_evaluator.py` — прогрев + два порога
- Retention 90 дней, fix enum `moderationstatus`

### Фаза 6 — Проверка по рынку ✅
- `market_price_checker.py`, `market_search.py`
- Поля market check в `DealModeration`
- Миграция `d4e5f6a7b8c9`

### Фаза 7 — UX бота ✅
- `bot/navigation/` — тексты, клавиатуры, «Назад», главное меню
- `/help`, админ-панель (кнопки), несколько админов
- Quick-add: ссылка или артикул (`/add`, `track_link_parser.py`)
- Пароль: 8–64 символа (бот + API)

---

## Логика скидок

### Прогрев (`DATA_COLLECTION_WARMUP_DAYS`)

| Условие | Поведение |
|---------|-----------|
| Нет записей в истории | Только парсер ≥ 15% |
| Прошло < N дней с первой записи | Только парсер |
| Иначе | Таблица ниже |

### После прогрева

| БД ≥ 20% | Парсер ≥ 15% | Результат |
|----------|--------------|-----------|
| ✅ | ✅ | Автопост |
| ✅ | ❌ | Пост «скидка по средней цене» |
| ❌ | ✅ | Модерация |
| ❌ | ❌ | Пропуск |

---

## Текущие проблемы (backlog)

| # | Проблема | Приоритет |
|---|----------|-----------|
| 1 | Ozon antibot: нужен приватный резидентный/мобильный IP | **Высокий — единственный открытый блокер парсинга.** Шэйред мобильный прокси не прошёл (3/3), headed-режим не помогает (в отличие от WB) — нужен выделенный/индивидуальный мобильный прокси или собственный телефон с SIM |
| 2 | WB краулер: 0 товаров | ✅ переписан на Playwright (headed) + чтение DOM, антибот `x-pow` обойдён |
| 3 | ЯМ: нет `highPrice` → нет скидки парсера | ✅ цена «до скидки» берётся из `apiary-patch` (`offerAnalytics.oldPrice`) |
| 4 | Мало товаров (~68 vs 480 макс.) | Средний — п.1 (Ozon) + `MAX_PRODUCTS_PER_CATEGORY` |
| 5 | `DATA_COLLECTION_WARMUP_DAYS=0` — строгий режим сразу | Низкий — выставить 3–7 на старте |
| 6 | `Dockerfile.api` не устанавливает Playwright/Chromium/Xvfb | Средний — `POST /deals/run` в api-контейнере упадёт на Ozon/WB без браузера; либо добавить зависимости, либо отключить эндпоинт в проде |
| 7 | Категории WB/ЯМ нужно периодически перепроверять | Низкий — числовые ID и верхнеуровневые URL иногда «уезжают» на другой контент (уже случалось дважды за это время), см. пример в README |

---

## Конфиг (.env) — актуальный пример

```env
TELEGRAM_CHANNEL_ID=-100xxxxxxxxxx
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=790067446,1395854084

MIN_PARSER_DISCOUNT_PERCENT=15
MIN_DATABASE_DISCOUNT_PERCENT=20
DATA_COLLECTION_WARMUP_DAYS=0
PRICE_HISTORY_RETENTION_DAYS=90

MARKET_CHECK_MIN_PRICE=10000
MARKET_CHECK_DISCOUNT_PERCENT=10
MARKET_CHECK_CATEGORIES=electronics,furniture,home

CRAWL_INTERVAL_MINUTES=30
MAX_PRODUCTS_PER_CATEGORY=20
DEALS_ENABLED=true
PROXY_LIST=
```

---

## Что не делаем

- Отдельные каналы по категориям
- Selenium (пока)
- Apify / платные парсеры (опционально позже)

---

## Статус

**В эксплуатации, 2 из 3 площадок полностью рабочие.** Scheduler каждые 30 мин. Бот с навигацией и quick-add.

- ✅ **Wildberries** — работает (Playwright headed + Xvfb, DOM-парсинг, без прокси)
- ✅ **Яндекс Маркет** — работает (без браузера, `httpx` + JSON-LD)
- ⚠️ **Ozon** — код готов, но канал будет постить 0 сделок по Ozon, пока не появится рабочий приватный резидентный/мобильный прокси (см. backlog #1)

Проверка: `/force_crawl` → отчёт в Telegram (там же видно разбивку по площадкам). Логи: `docker logs telegram_bot`. Смоук-тесты: `python -m scripts.smoke_wb_crawl`, `python -m scripts.smoke_ozon_crawl`.
