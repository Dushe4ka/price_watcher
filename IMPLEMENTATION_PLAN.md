# План реализации: Price Watcher / Deal Channel Bot

## Финишная картина

**Один Telegram-канал** автоматически получает посты о скидках с Wildberries, Ozon и Яндекс Маркета.

Категории различаются хештегами. Все карточки с обхода сохраняются в БД. После недели прогрева основной критерий скидки — сравнение с **средней ценой за 90 дней**.

### Компоненты

| Компонент | Назначение |
|-----------|------------|
| `src/parsers/` | Парсинг карточки товара (WB, Ozon, YM) |
| `src/crawlers/` | Обход категорий → список product_id |
| `src/services/deal_pipeline.py` | crawl → parse → save → evaluate → post/moderate |
| `src/services/discount_evaluator.py` | Логика прогрева и двух порогов скидки |
| `src/crud/price_tracking.py` | TrackedProduct + ProductPriceHistory |
| `src/crud/deal_moderation.py` | Журнал решений по публикации |
| `src/models/posted_deal.py` | Дедупликация опубликованных товаров |
| `config/monitored_categories.yaml` | Категории для мониторинга |
| `bot/deals_scheduler.py` | APScheduler: периодический запуск pipeline |
| `bot/handlers/deal_moderation.py` | Inline-кнопки «Принять» / «Отклонить» |
| API `/deals/*` | Статистика и ручной запуск |

### Конфиг (.env)

```env
TELEGRAM_CHANNEL_ID=-100xxxxxxxxxx
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=790067446

MIN_PARSER_DISCOUNT_PERCENT=15
MIN_DATABASE_DISCOUNT_PERCENT=20
PRICE_HISTORY_RETENTION_DAYS=90
DATA_COLLECTION_WARMUP_DAYS=7

CRAWL_INTERVAL_MINUTES=30
DEALS_ENABLED=true
MAX_PRODUCTS_PER_CATEGORY=20
CATEGORIES_CONFIG_PATH=config/monitored_categories.yaml
PROXY_LIST=
```

---

## Фазы выполнения

### Фаза 0 — Инфраструктура ✅
- [x] Зависимости: httpx, selectolax, pyyaml
- [x] Расширение Settings + .env.example
- [x] Docker Compose (db, api, bot, nginx)

### Фаза 1 — Парсеры карточек ✅
- [x] `src/parsers/base.py`, `utils.py`
- [x] `wildberries.py`, `ozon.py`, `yandex_market.py`

### Фаза 2 — Краулеры категорий ✅
- [x] `src/crawlers/ozon.py`, `wildberries.py`, `yandex_market.py`
- [x] `config/monitored_categories.yaml` (8 категорий × 3 МП)

### Фаза 3 — Pipeline ✅
- [x] `src/services/post_formatter.py`
- [x] `src/services/deal_pipeline.py`
- [x] CRUD `posted_deal`

### Фаза 4 — Bot + Scheduler ✅
- [x] `bot/deals_scheduler.py` (post_init)
- [x] Admin: `/deals_status`, `/force_crawl`
- [x] `bot/commands.py`

### Фаза 5 — API + Docker ✅
- [x] API endpoints `/deals`
- [x] Dockerfile.bot / Dockerfile.api
- [x] Enum Marketplace (+ yandex_market)
- [x] Миграция `posteddeal`

### Фаза 6 — История цен и модерация ✅
- [x] Модели: `TrackedProduct`, `ProductPriceHistory`, `DealModeration`
- [x] Миграции `b2c3d4e5f6a7`, `c3d4e5f6a7b8` (timestamptz fix)
- [x] `discount_evaluator.py` — прогрев 7 дней + два порога
- [x] Сохранение всех цен при обходе, retention 90 дней
- [x] Модерация админу в ЛС (принять / отклонить)
- [x] Журнал всех решений в `dealmoderation`

---

## Логика после прогрева

| БД ≥ 20% | Парсер ≥ 15% | Результат |
|----------|--------------|-----------|
| ✅ | ✅ | Автопост |
| ✅ | ❌ | Автопост с пометкой «скидка по средней цене» |
| ❌ | ✅ | Модерация админу |
| ❌ | ❌ | Пропуск |

---

## Что не делаем сейчас

- Старые user/track handlers — остаются как legacy
- Отдельные каналы по категориям — один канал + хештеги
- Selenium fallback — только при блокировке API

---

## Статус: готово к эксплуатации

Проект развёрнут через Docker. Scheduler работает каждые 30 минут.  
Для проверки: `/force_crawl` в боте.
