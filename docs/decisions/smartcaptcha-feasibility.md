# SmartCaptcha: границы frictionless-режима

**Дата:** 2026-08-22
**Статус:** принято для первой поставки

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
Marketplace-specific structural validator выполняется downstream в Task 11 и
только он классифицирует полученные данные. Код причины
`CHALLENGE_UNSUPPORTED` также формируется на границе marketplace source, тогда
как CAPTCHA-контракт Task 9 сохраняет enum `ChallengeResolution`.

## Источники

Context7 был проверен 2026-08-22, но официальной библиотеки или документации
Yandex SmartCaptcha там нет; найден только сторонний Angular wrapper с низкой
репутацией, поэтому он не использовался.

Первичные источники:

- [Методы установки виджета и события subscribe](https://yandex.cloud/en/docs/smartcaptcha/concepts/widget-methods), обновлено 2026-05-15.
- [Invisible CAPTCHA и execute](https://yandex.cloud/en/docs/smartcaptcha/concepts/invisible-captcha), актуальная документация Yandex Cloud.

OhMyCaptcha не поддерживает SmartCaptcha в pinned snapshot. Подстановка
выдуманного token, client key или server key запрещена.
