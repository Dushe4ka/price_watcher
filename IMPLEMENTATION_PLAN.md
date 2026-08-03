# План реализации: Price Watcher / LuluSaleBot

## Финишная картина

**Один Telegram-канал** с автопостингом скидок. Категории — хештегами. Личный трекер цен — в том же боте.

---

## Выполнено

### Фаза 0 — Инфраструктура ✅
- Docker Compose: `db`, `api`, `telegram_bot`, `nginx`
- Settings, `.env.example`, миграции Alembic

### Фаза 1 — Парсеры карточек ✅
- `src/parsers/`: WB, Ozon, Yandex Market
- `ParsedProduct`: цена, original_price, discount_percent

### Фаза 2 — Краулеры категорий ✅ (с ограничениями)
- `src/crawlers/` + `config/monitored_categories.yaml` (8 категорий × 3 МП)
- **Фактически работает:** Яндекс Маркет
- **Не работает без доработок:** Ozon (403), WB (пустые ответы API)

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
| 1 | Ozon antibot при VPN/DC IP | Высокий — `PROXY_LIST` RU + entrypoint-api (сделано в коде) |
| 2 | WB краулер: 0 товаров | ✅ nested `data.products` / `salePriceU` |
| 3 | ЯМ: нет `highPrice` → нет скидки парсера | Высокий — fallback в HTML |
| 4 | Мало товаров (~68 vs 480 макс.) | Средний — п.1–2 + `MAX_PRODUCTS_PER_CATEGORY` |
| 5 | `DATA_COLLECTION_WARMUP_DAYS=0` — строгий режим сразу | Низкий — выставить 3–7 на старте |

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

**В эксплуатации.** Scheduler каждые 30 мин. Бот с навигацией и quick-add.

Проверка: `/force_crawl` → отчёт в Telegram. Логи: `docker logs telegram_bot`.
