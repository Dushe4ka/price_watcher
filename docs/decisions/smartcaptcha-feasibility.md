# SmartCaptcha: границы frictionless-режима

**Дата:** 2026-08-22
**Статус:** принято и реализовано; поставляется выключенным по умолчанию
(`SMARTCAPTCHA_MODE=disabled`)

## Решение

SmartCaptcha по умолчанию отключена. Режим `frictionless` разрешён только для
уже загруженного на арендованной marketplace-странице объекта
`window.smartCaptcha` и заранее известного публичного `widgetId`. Обработчик не
создаёт browser, context, page или widget и не загружает внешний скрипт.

Официальный API допускает `execute()` без идентификатора для первого widget, но
`subscribe(widgetId, event, callback)` требует конкретный `widgetId`. Поэтому
автоматизация на сторонней странице без заранее известного доверенного ID
недоступна и завершается fail-closed. Мы не используем undocumented
`subscribe(undefined, ...)`, DOM scraping или private provider state.

`SMARTCAPTCHA_WIDGET_ID` принимает только 1–128 символов из ограниченного
набора `A-Z`, `a-z`, `0-9`, `.`, `_`, `:`, `-`. Пустое значение означает, что
обработчик отключён даже при `SMARTCAPTCHA_MODE=frictionless`. Значение является
публичным ID, а не callback token, client key или server key.

## Разрешённый сценарий

Обработчик на той же `Page`:

1. подписывается через документированный `subscribe(widgetId, ...)` на
   `challenge-visible`, `network-error`, `javascript-error`, `success` и
   `token-expired`;
2. вызывает существующий `execute(widgetId)`;
3. никогда не принимает и не сохраняет аргумент token у callback `success`;
4. возвращает `CHALLENGE_UNSOLVABLE` для visible/error/expired/missing API;
5. после `success` повторно проверяет challenge на той же `Page` и возвращает
   `SOLVED` только после исчезновения challenge.

Интерактивное задание не кликается. На timeout/cancellation страница закрывается,
чтобы отменённый JavaScript не мог позднее изменить её состояние.

Строгий structural marker (`data-challenge-type=slider`, соответствующий class
или id) сохраняет `is_interactive` даже для challenge типа `UNKNOWN`. Такой
challenge coordinator отклоняет до любого JavaScript-вызова. Текстовые слова
`image`, `slider` или `audio` сами по себе интерактивность не активируют.

Callback lifecycle учитывает синхронные события во время `subscribe`: terminal
failure прекращает дальнейшие подписки и запрещает `execute`, а возвращённый
после callback unsubscribe вызывается сразу. Синхронный `success` считается
предварительным до завершения всех подписок и успешного возврата из `execute`;
видимый challenge, error, expiration или exception в том же setup-цикле имеет
fail-closed приоритет.

## Граница с marketplace-валидацией

Исчезновение challenge доказывает только завершение CAPTCHA-этапа. Оно не
доказывает корректность HTML/JSON маркетплейса и не означает, что товар найден.
Marketplace-specific structural validator (`src/marketplaces/validation.py`)
выполняется downstream и только он классифицирует полученные данные. Код
причины `challenge_unsupported` формируется на границе marketplace source
(`src/marketplaces/sources/browser.py`), тогда как CAPTCHA-контракт
(`src/captcha/models.py`) сохраняет enum `ChallengeResolution`.

## Источники

Context7 был проверен 2026-08-22, но официальной библиотеки или документации
Yandex SmartCaptcha там нет; найден только сторонний Angular wrapper с низкой
репутацией, поэтому он не использовался.

Первичные источники:

- [Методы установки виджета и события subscribe](https://yandex.cloud/en/docs/smartcaptcha/concepts/widget-methods), обновлено 2026-05-15.
- [Invisible CAPTCHA и execute](https://yandex.cloud/en/docs/smartcaptcha/concepts/invisible-captcha), актуальная документация Yandex Cloud.

OhMyCaptcha не поддерживает SmartCaptcha в pinned snapshot. Подстановка
выдуманного token, client key или server key запрещена.

## Где это в коде

| Файл | Роль |
|------|------|
| `src/captcha/smartcaptcha.py` | `SmartCaptchaMode`, валидация `widgetId`, сам обработчик |
| `src/captcha/coordinator.py` | Разбор challenge строго на переданной `Page`, повторная детекция после действия обработчика |
| `src/captcha/detector.py` | Определение типа challenge и признака интерактивности |
| `src/core/config.py` | `SMARTCAPTCHA_MODE`, `SMARTCAPTCHA_CLIENT_KEY`, `SMARTCAPTCHA_WIDGET_ID` и `validate_smartcaptcha_widget_id` |
| `src/marketplaces/registry.py` | `build_challenge_coordinator` — обработчик подключается только при явной конфигурации |

Инвариант «одна и та же `Page` / один и тот же `Context`», в который этот
документ встроен, и таксономия исходов описаны в
`docs/architecture/marketplace-fallback.md` (разделы 8 и 12). Операционная
сторона — `docs/runbooks/troubleshooting.md`, раздел 5.

## Что осталось непроверенным

Frictionless-режим ни разу не запускался против живой площадки: для этого
нужен доверенный публичный `SMARTCAPTCHA_WIDGET_ID`, которого у проекта нет.
Реализация покрыта тестами (`tests/test_smartcaptcha.py`,
`tests/test_challenge_coordinator.py`), но живой прогон — открытый пункт.
