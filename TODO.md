# Идеи для доработки

## Яндекс.Маркет
- [x] Парсер и краулер переведены на новую URL-схему (`/card/<slug>/<id>`) и извлечение цены «до скидки» из `<noframes data-apiary="patch">` (`collections.offerAnalytics`). Headless-браузер не понадобился — страницы по-прежнему server-rendered.

## Ozon
- [x] Playwright + entrypoint-api.bx (обход antibot при `abt_att`).
- [ ] Для стабильного продакшена: `PROXY_LIST` (RU residential/mobile), при жёстком IP-бане — `OZON_PROXY_REQUIRED=true`. Шэйред мобильный прокси (proxy.market) не прошёл 2/2 попытки — нужен приватный/выделенный мобильный IP.

## Wildberries
- [x] `search.wb.ru` теперь требует proof-of-work антибот-токен (`x-pow`) на каждый запрос — обычный HTTP больше не проходит ни с одним IP. Переведено на Playwright, но только **headed**-режим проходит antibot-челлендж WB (headless — любой вариант — зависает навсегда, IP тут ни при чём). Краулер и парсер читают данные прямо из DOM (`.product-card`), без API и без текстового поиска. См. `src/wb/`, `Dockerfile.bot` (Xvfb).

## Marketplace fallback

- [x] Единый слой `src/marketplaces/` с настраиваемыми цепочками источников, structural `empty`, безопасными логами и персистентными профилями по паре `(role, marketplace)`. Архитектура: `docs/architecture/marketplace-fallback.md`.
- [x] Раздельные таймауты `MARKETPLACE_TOTAL_TIMEOUT_SEC` (на источник) и `MARKETPLACE_OPERATION_TIMEOUT_SEC` (на всю цепочку) + cross-field валидация.
- [x] Документация и runbooks (`docs/runbooks/`), контракт документации проверяется `tests/test_documented_configuration.py`.
- [ ] Apify-этап встроен, но маппинг датасета — собственная синтетическая схема; против живого actor не проверялся. Нужен один реальный прогон с `APIFY_TOKEN` и actor ID, прежде чем на него полагаться.
- [ ] `scripts/smoke_marketplace_stack.py --mode compose` ни разу не отрабатывал против реального Docker-демона: `docker-compose.yml` фиксирует `container_name`, и на тестовых хостах эти имена были заняты.
- [ ] SmartCaptcha frictionless реализована, но по умолчанию выключена и на живой площадке не проверялась — нужен доверенный публичный `SMARTCAPTCHA_WIDGET_ID`.
