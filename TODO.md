# Идеи для доработки

## Яндекс.Маркет
- [x] Парсер и краулер переведены на новую URL-схему (`/card/<slug>/<id>`) и извлечение цены «до скидки» из `<noframes data-apiary="patch">` (`collections.offerAnalytics`). Headless-браузер не понадобился — страницы по-прежнему server-rendered.

## Ozon
- [x] Playwright + entrypoint-api.bx (обход antibot при `abt_att`).
- [ ] Для стабильного продакшена: `PROXY_LIST` (RU residential/mobile), при жёстком IP-бане — `OZON_PROXY_REQUIRED=true`. Шэйред мобильный прокси (proxy.market) не прошёл 2/2 попытки — нужен приватный/выделенный мобильный IP.

## Wildberries
- [x] `search.wb.ru` теперь требует proof-of-work антибот-токен (`x-pow`) на каждый запрос — обычный HTTP больше не проходит ни с одним IP. Переведено на Playwright, но только **headed**-режим проходит antibot-челлендж WB (headless — любой вариант — зависает навсегда, IP тут ни при чём). Краулер и парсер читают данные прямо из DOM (`.product-card`), без API и без текстового поиска. См. `src/wb/`, `Dockerfile.bot` (Xvfb).
