# Идеи для доработки

## Яндекс.Маркет
- [ ] Парсер не работает — ЯМ отдаёт SPA-оболочку без данных. Нужен headless-браузер (Playwright) или партнёрский API для извлечения цен, рейтингов и отзывов.

## Ozon
- [x] Playwright + entrypoint-api.bx (обход antibot при `abt_att`).
- [ ] Для стабильного продакшена: `PROXY_LIST` (RU residential/mobile), при жёстком IP-бане — `OZON_PROXY_REQUIRED=true`.
