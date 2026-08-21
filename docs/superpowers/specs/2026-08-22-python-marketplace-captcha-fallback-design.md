# Python Marketplace CAPTCHA Fallback Design

**Дата:** 2026-08-22
**Статус:** утверждено пользователем 2026-08-22
**Репозиторий:** `Dushe4ka/price_watcher`
**Ветка реализации:** `feature/marketplace-captcha-fallback`

## 1. Цель

Добавить в Price Watcher единый, проверяемый и безопасный fallback-механизм
получения данных с Wildberries, Ozon и Yandex Market. Механизм должен отличать
корректный пустой результат от блокировки, CAPTCHA, rate limit, сетевой ошибки и
изменения схемы; при необходимости он использует существующую browser-сессию,
адаптированный pinned snapshot OhMyCaptcha и необязательный Apify fallback.

Решение предназначено для локального запуска и VPS. Все изменения проходят TDD,
независимое ревью, отдельный Conventional Commit, push и обновление документации.

## 2. Принятые решения

### 2.1 Топология

Первая поставка использует **in-process Python `MarketplaceService`**. Отдельный
HTTP/browser gateway не создаётся: текущие crawler, parser, API и Telegram bot уже
находятся в одном Python codebase, поэтому сетевая граница добавила бы очередь,
polling, авторизацию и сериализацию без доменной пользы.

API и bot являются разными процессами и получают разные persistent profile roots:

```text
{BROWSER_PROFILE_ROOT}/{MARKETPLACE_RUNTIME_ROLE}/{marketplace}
```

Допустимые роли: `local`, `api`, `bot`. Один каталог профиля не может быть открыт
двумя процессами одновременно; это обеспечивается OS-level lock и проверкой
`WEB_CONCURRENCY=1`.

Если позднее потребуется единая browser identity для API и bot либо строгая
изоляция browser crashes/RAM, это будет отдельное решение о browser gateway, а не
скрытая часть текущей реализации.

### 2.2 Цепочки источников

Рабочие значения по умолчанию:

| Площадка | Цепочка |
|---|---|
| Wildberries | `browser,apify` |
| Ozon | `browser,apify` |
| Yandex Market | `public,browser,apify` |

Конфигурация принимает `public`, `browser`, `apify` в любом валидном порядке.
Отсутствующий или технически не доказанный источник возвращает `DISABLED`, а не
имитирует `EMPTY`. Поэтому для WB/Ozon можно явно включить `public`, но до появления
проверенного transport он будет пропущен как disabled.

### 2.3 CAPTCHA scope

Challenge обнаруживается, обрабатывается и повторно проверяется на **той же
`Page` и в том же persistent `Context`**, где выполняется marketplace-операция.
Это сохраняет cookies, IP, localStorage и browser fingerprint.

В первой поставке автоматически обрабатываются только deterministic
checkbox/frictionless сценарии. Интерактивные image/audio/slider задачи без
отдельно утверждённого model/provider завершаются как `CHALLENGE_UNSOLVABLE`, после
чего fallback может перейти к Apify. Нельзя создавать новую Page/Context, сообщать
об успехе только по наличию token или подставлять выдуманный token.

Pinned snapshot OhMyCaptcha остаётся неизменяемым в `vendor/ohmycaptcha`. Основной
код импортирует только `src.captcha.ohmycaptcha_adapter`; vendor загружается через
synthetic namespace, без добавления vendor root в `sys.path`.

SmartCaptcha отсутствует в pinned snapshot. По умолчанию режим `disabled`.
Опциональный `frictionless` режим может вызвать существующий на странице
`window.smartCaptcha.execute()` и наблюдать официальные callbacks. Visible/slider
challenge без доказанного доступа к защищённым данным считается нерешённым.

### 2.4 Версии

- Python 3.12.
- Основной runtime сохраняет `playwright==1.53.0`.
- Основной Ozon runtime сохраняет `patchright==1.61.2`.
- Vendor pin `playwright==1.49.1` не устанавливается в основное окружение.
- `vendor/ohmycaptcha/**` не редактируется.
- При несовместимости private vendor contract основной runtime не понижается:
  обновление pinned snapshot или изолированный gateway требуют отдельного решения.

## 3. Доменная модель

Новые контракты располагаются в `src/marketplaces/contracts.py`.

```python
MarketplaceName = Literal['wildberries', 'ozon', 'yandex_market']

class SourceName(StrEnum):
    PUBLIC = 'public'
    BROWSER = 'browser'
    APIFY = 'apify'

class MarketplaceOperation(StrEnum):
    CRAWL_CATEGORY = 'crawl_category'
    PARSE_PRODUCT = 'parse_product'
    SEARCH_PRODUCTS = 'search_products'

class SourceOutcome(StrEnum):
    SUCCESS = 'success'
    EMPTY = 'empty'
    NOT_FOUND = 'not_found'
    CHALLENGE = 'challenge'
    RATE_LIMITED = 'rate_limited'
    TRANSPORT_ERROR = 'transport_error'
    PARSE_DRIFT = 'parse_drift'
    AUTH_ERROR = 'auth_error'
    INVALID_CONFIG = 'invalid_config'
    DISABLED = 'disabled'
```

Каждый source возвращает `SourceResult[T]` с одним безопасным `SourceAttempt`.
Fallback агрегирует попытки в `MarketplaceResult[T]`. Публичные запросы типизированы:

```python
CategoryRequest(category_slug: str, limit: int)
ProductRequest(product_id: str)
SearchRequest(query: str, limit: int, page: int = 1)
```

DTO не содержат произвольный URL, proxy или actor payload. Product URL используется
только для `extract_product_id`; навигационный URL строится кодом из product ID.
Category URL берётся из доверенной конфигурации и проходит host allowlist.

## 4. Fallback и retry

`FallbackExecutor` вызывает каждый сконфигурированный source максимум один раз.

Терминальные source outcomes:

- `SUCCESS`;
- структурно доказанный `EMPTY`;
- product-level `NOT_FOUND`.

Переход к следующему source:

- `DISABLED`;
- `CHALLENGE`;
- `RATE_LIMITED`;
- `TRANSPORT_ERROR`;
- `PARSE_DRIFT`;
- source-local `AUTH_ERROR`;
- source-local `INVALID_CONFIG`.

Единственным владельцем транспортных повторов является `SourceRetryExecutor`.
Максимум — две транспортные попытки одного source внутри общего deadline. Старые
parser decorators и client-local retry loops удаляются с мигрированных путей, чтобы
число запросов не перемножалось.

`EMPTY` разрешён только после marketplace-specific structural validation. CAPTCHA,
HTTP 429, timeout, invalid JSON/HTML и selector/schema drift не могут стать пустым
результатом.

## 5. Browser lifecycle и безопасность

`BrowserSessionManager` лениво запускает persistent contexts и выдаёт lease на одну
операцию. Один marketplace сериализован; разные marketplaces могут работать
параллельно. Task Page закрывается после операции, persistent Context — при idle
timeout или lifecycle shutdown.

- Ozon продолжает использовать Patchright, headed Chrome и persistent context.
- WB переходит с ephemeral context на Playwright persistent context.
- Yandex получает отдельный Playwright persistent context для browser fallback.
- FastAPI открывает и закрывает manager через lifespan.
- Telegram bot закрывает manager через async `post_shutdown` callback.
- Browser images работают non-root, через `tini`, Xvfb, shared memory и seccomp.
- Chromium sandbox не отключается.

Навигация разрешает только HTTPS и точные marketplace hosts. Запрещены userinfo,
IP literals, нестандартные ports и suffix tricks. Redirect main frame повторно
валидируется. Popups закрываются. Profiles имеют mode `0700` и никогда не задаются
пользовательским запросом.

## 6. Источники

### 6.1 Native/public

Существующие чистые мапперы `src/ozon/parse_widgets.py`, `src/wb/dom_extract.py` и
`src/parsers/ym_api.py` остаются каноническими. Validators классифицируют synthetic
fixtures как `VALID_WITH_ITEMS`, `VALID_EMPTY`, `CHALLENGE` или `DRIFT`.

### 6.2 Browser

`OzonBrowserSource`, `WildberriesBrowserSource` и `YandexMarketBrowserSource`
получают `BrowserSessionManager` и `ChallengeCoordinator` через dependency
injection. Навигация, challenge handling, повторная проверка и extraction используют
один и тот же Page object. HTML/JSON ограничены `MARKETPLACE_MAX_CONTENT_BYTES`.

### 6.3 Apify

`ApifyClient` выбирает actor ID только из Settings и строит fixed input code-side.
Входной DTO не может передать URL, actor ID, proxy или произвольный JSON.

- отсутствующий token/actor: `DISABLED`;
- 401/403: `AUTH_ERROR`;
- 429: `RATE_LIMITED`;
- network/5xx: `TRANSPORT_ERROR`;
- schema mismatch: `PARSE_DRIFT`;
- валидный пустой dataset: `EMPTY`.

До задания утверждённых actor IDs и бюджета Apify остаётся реализованным, покрытым
controlled tests, но disabled в runtime.

## 7. Интеграция с приложением

```text
DealPipeline / track API / MarketPriceChecker
  -> MarketplaceService
  -> FallbackExecutor
  -> public | browser | apify adapter
  -> MarketplaceResult[T]
  -> safe aggregate diagnostics
```

`BaseParser.parse_product()` и `MarketplaceCrawler.crawl_category()` сохраняются как
compatibility wrappers. Новые consumers используют `parse_product_result()` и
`crawl_category_result()`, чтобы не терять attempts. `DealPipeline` считает aggregate
failure ошибкой, но не считает корректный empty ошибкой.

## 8. Логи и секреты

Разрешённые поля telemetry: marketplace, operation, source, outcome, duration,
item count, safe error code и bounded retry delay.

Запрещено логировать query, product ID/URL, title, raw body/HTML, cookies,
Authorization, proxy host/credentials, CAPTCHA token/value/length, provider/model key
и исходное exception message. Exceptions используют фиксированные safe codes.

`.env`, browser profiles, screenshots, traces, videos и auth state не входят в Git
или Docker build context. Секреты Settings используют `SecretStr`; их `repr` и
ValidationError не раскрывают значения.

## 9. Проверка и поставка

- Основной test runner остаётся `unittest`; тестовый bootstrap не требует `.env`.
- Каждый production change начинается с теста, который наблюдался красным.
- Unit/controlled integration tests не обращаются к живым marketplaces.
- Controlled fixture server покрывает clean, empty, challenge, rate-limit, redirect,
  oversized content и timeout.
- Live probe возможен только при `LIVE_MARKETPLACE_TESTS=1`, выполняет одну bounded
  операцию и выводит только агрегаты.
- CI не получает marketplace/provider credentials и не выполняет live traffic.
- Проверяются оба Docker image, Compose policy, persistent profile restart,
  graceful shutdown и отсутствие секретов.
- Upstream OhMyCaptcha tests запускаются отдельно в `.venv-ohmycaptcha`.
- Каждая задача имеет отдельный commit и push. Merge в `develop`/`main` не входит в
  объём работ без отдельной команды пользователя.

## 10. Критерии готовности

Работа готова, когда все три площадки используют структурированные outcomes,
fallback не умножает запросы, browser challenge остаётся в одной Page/Context,
profiles переживают restart, SmartCaptcha и Apify имеют явные gates, controlled
tests/Compose проходят, документация описывает локальный и VPS запуск, а ветка
прошла независимое task-by-task и итоговое ревью.
