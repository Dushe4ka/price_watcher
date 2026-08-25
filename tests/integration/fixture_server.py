"""Loopback HTTP fixture server for controlled marketplace scenarios.

The server is a small stdlib ``asyncio`` HTTP/1.1 responder bound to an
ephemeral 127.0.0.1 port. It never speaks to anything outside the test
process and it is the only origin the controlled browser context is allowed
to receive bytes from.

Scenarios are pure functions of the incoming request, so the same fixture
serves clean success, valid-empty, challenge-then-result, rate limiting,
redirects (safe and unsafe), oversized content and delayed responses without
any shared mutable state beyond an explicit per-server counter.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

# The controlled router maps this host to ``/attacker`` so an unsafe
# redirect target still answers with tempting bytes: a source that followed
# it would visibly succeed, which is exactly what the redirect tests deny.
UNSAFE_REDIRECT_HOST = 'attacker.invalid'
UNSAFE_REDIRECT_URL = f'https://{UNSAFE_REDIRECT_HOST}/harvest'

_MAX_REQUEST_BYTES = 64 * 1024
_READ_TIMEOUT_SEC = 10.0

_STATUS_TEXT = {
    200: 'OK',
    302: 'Found',
    404: 'Not Found',
    429: 'Too Many Requests',
    500: 'Internal Server Error',
    503: 'Service Unavailable',
}


@dataclass(frozen=True, slots=True)
class FixtureRequest:
    """One parsed request line plus headers, with the body discarded."""

    method: str
    path: str
    query: str
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class FixtureResponse:
    """A canned response, optionally delayed before the first byte."""

    status: int = 200
    body: bytes = b''
    content_type: str = 'text/html; charset=utf-8'
    headers: tuple[tuple[str, str], ...] = ()
    delay_sec: float = 0.0


Scenario = Callable[[FixtureRequest, 'FixtureServer'], FixtureResponse]


@dataclass(slots=True)
class RequestRecord:
    """What the fixture actually served, for exact end-to-end counting."""

    method: str
    path: str
    status: int


class FixtureServer:
    """An ephemeral loopback origin serving one named scenario."""

    def __init__(self, name: str, scenario: Scenario) -> None:
        self.name = name
        self._scenario = scenario
        self._server: asyncio.AbstractServer | None = None
        self._origin = ''
        self.records: list[RequestRecord] = []

    @property
    def origin(self) -> str:
        """Return ``http://127.0.0.1:<port>`` for the running server."""
        if not self._origin:
            raise RuntimeError('fixture server is not running')
        return self._origin

    def url(self, path: str) -> str:
        """Return an absolute fixture URL for one server-relative path."""
        if not path.startswith('/'):
            path = f'/{path}'
        return f'{self.origin}{path}'

    def count(self, path: str | None = None) -> int:
        """Count served requests, optionally for one exact path."""
        if path is None:
            return len(self.records)
        return sum(1 for record in self.records if record.path == path)

    def paths(self) -> tuple[str, ...]:
        """Return every served path in arrival order."""
        return tuple(record.path for record in self.records)

    async def start(self) -> None:
        """Bind the loopback listener on an ephemeral port."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host='127.0.0.1',
            port=0,
        )
        socket_name = self._server.sockets[0].getsockname()
        self._origin = f'http://127.0.0.1:{socket_name[1]}'

    async def stop(self) -> None:
        """Close the listener and wait for it to release the port."""
        server = self._server
        self._server = None
        self._origin = ''
        if server is None:
            return
        server.close()
        await server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await asyncio.wait_for(
                _read_request(reader),
                timeout=_READ_TIMEOUT_SEC,
            )
            if request is None:
                return
            response = self._scenario(request, self)
            self.records.append(
                RequestRecord(
                    method=request.method,
                    path=request.path,
                    status=response.status,
                ),
            )
            if response.delay_sec > 0:
                await asyncio.sleep(response.delay_sec)
            writer.write(_encode_response(response))
            await writer.drain()
        except asyncio.CancelledError:
            raise
        except (
            asyncio.TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            OSError,
            UnicodeDecodeError,
        ):
            # A client that goes away mid-request is a normal teardown
            # event here, not a fixture failure.
            return
        finally:
            try:
                writer.close()
            except (ConnectionError, OSError):
                return


@asynccontextmanager
async def fixture_server(
    scenario_name: str,
    **options: object,
) -> AsyncIterator[FixtureServer]:
    """Run one named scenario on an ephemeral loopback port."""
    try:
        builder = _SCENARIOS[scenario_name]
    except KeyError:
        raise ValueError(
            f'unknown fixture scenario: {scenario_name}',
        ) from None
    server = FixtureServer(scenario_name, builder(**options))
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


async def _read_request(
    reader: asyncio.StreamReader,
) -> FixtureRequest | None:
    head = await reader.readuntil(b'\r\n\r\n')
    if len(head) > _MAX_REQUEST_BYTES:
        return None
    lines = head.decode('latin-1').split('\r\n')
    parts = lines[0].split(' ')
    if len(parts) < 2:
        return None
    method, target = parts[0], parts[1]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(':')
        if separator:
            headers[name.strip().lower()] = value.strip()
    length = headers.get('content-length', '')
    if length.isdigit():
        await reader.readexactly(min(int(length), _MAX_REQUEST_BYTES))
    path, _, query = target.partition('?')
    return FixtureRequest(
        method=method,
        path=path,
        query=query,
        headers=headers,
    )


def _encode_response(response: FixtureResponse) -> bytes:
    reason = _STATUS_TEXT.get(response.status, 'OK')
    lines = [f'HTTP/1.1 {response.status} {reason}']
    lines.append(f'Content-Type: {response.content_type}')
    lines.append(f'Content-Length: {len(response.body)}')
    lines.append('Cache-Control: no-store')
    lines.append('Connection: close')
    for name, value in response.headers:
        lines.append(f'{name}: {value}')
    head = '\r\n'.join(lines).encode('latin-1')
    return head + b'\r\n\r\n' + response.body


def _html(markup: str) -> bytes:
    return markup.encode('utf-8')


def _product_ld_json(product_id: str, price: str) -> str:
    return json.dumps(
        {
            '@context': 'https://schema.org',
            '@type': 'Product',
            'name': f'Товар {product_id}',
            'url': f'https://market.yandex.ru/card/x/{product_id}',
            'image': f'https://market.yandex.ru/i/{product_id}.jpg',
            'offers': {
                '@type': 'Offer',
                'price': price,
                'priceCurrency': 'RUB',
                'availability': 'https://schema.org/InStock',
            },
        },
        ensure_ascii=False,
    )


def clean_product_page(
    product_ids: tuple[str, ...] = ('1017', '2024'),
    filler: int = 0,
) -> str:
    """Build a Yandex-Market-shaped page carrying JSON-LD products.

    The markup deliberately avoids every marker in the shared challenge
    vocabulary, so ``validate_yandex_html`` classifies it as valid.
    """
    blocks = '\n'.join(
        '<script type="application/ld+json">'
        f'{_product_ld_json(product_id, "1990")}'
        '</script>'
        for product_id in product_ids
    )
    padding = f'<div class="pad">{"т" * filler}</div>' if filler else ''
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<title>Каталог</title></head><body>'
        '<h1 data-zone-name="catalog">Каталог товаров</h1>'
        f'{blocks}{padding}'
        '</body></html>'
    )


def empty_page() -> str:
    """Build a structurally valid page that legitimately has no items."""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<title>Пусто</title></head><body>'
        '<div data-zone-name="searchempty">Ничего не найдено</div>'
        '</body></html>'
    )


def wall_page() -> str:
    """Build an antibot wall whose widget is resolvable inside the page.

    The widget is a Turnstile-shaped, non-interactive marker, which the
    production detector classifies deterministically. Solving it in the page
    fetches the real payload from this same fixture origin and removes the
    widget, so a re-detection is genuinely clean rather than assumed.
    """
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<title>Проверка</title></head><body>'
        '<div class="cf-turnstile" data-sitekey="0x-test-widget">'
        '<span id="verify-box" role="button">Продолжить</span>'
        '</div>'
        '<script>\n'
        'document.getElementById("verify-box")'
        '.addEventListener("click", async () => {\n'
        '  const answer = await fetch("/solved", '
        '{credentials: "omit"});\n'
        '  const payload = await answer.text();\n'
        '  const node = document.createElement("script");\n'
        '  node.type = "application/ld+json";\n'
        '  node.textContent = payload;\n'
        '  document.body.appendChild(node);\n'
        '  document.querySelector(".cf-turnstile").remove();\n'
        '  document.body.setAttribute("data-verified", "1");\n'
        '});\n'
        '</script>'
        '</body></html>'
    )


def navigating_page(target: str) -> str:
    """Build a page that moves the main frame to ``target`` on load.

    The redirect scenarios use a renderer-initiated navigation rather than a
    ``302``: Playwright does not pause the follow-up request of a *fulfilled*
    HTTP redirect, so a ``302`` would send the browser to the real network,
    which this suite must never do. A client-side navigation is paused by the
    same route interception, stays inside the controlled topology and drives
    exactly the same production guards (``framenavigated`` validation in the
    lease and ``page.url`` re-validation in the source).
    """
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<title>Переход</title></head><body>'
        '<p>Переход</p>'
        f'<script>location.replace({json.dumps(target)});</script>'
        '</body></html>'
    )


def attacker_page() -> str:
    """Build bait: an off-host page that parses cleanly if it is followed."""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<title>Bait</title></head><body>'
        '<script type="application/ld+json">'
        f'{_product_ld_json("9999", "1")}'
        '</script></body></html>'
    )


def _clean_scenario(**_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del server
        if request.path == '/attacker':
            return FixtureResponse(body=_html(attacker_page()))
        return FixtureResponse(body=_html(clean_product_page()))

    return scenario


def _valid_empty_scenario(**_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del request, server
        return FixtureResponse(body=_html(empty_page()))

    return scenario


def _challenge_then_result_scenario(**_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del server
        if request.path == '/solved':
            return FixtureResponse(
                body=_product_ld_json('1017', '1990').encode('utf-8'),
                content_type='application/json; charset=utf-8',
            )
        return FixtureResponse(body=_html(wall_page()))

    return scenario


def _rate_limit_scenario(retry_after: int = 7, **_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del request, server
        return FixtureResponse(
            status=429,
            body=_html('<html><body>slow down</body></html>'),
            headers=(('Retry-After', str(retry_after)),),
        )

    return scenario


def _transport_error_scenario(**_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del request, server
        return FixtureResponse(
            status=500,
            body=_html('<html><body>upstream unavailable</body></html>'),
        )

    return scenario


def _redirect_safe_scenario(
    marketplace_origin: str = 'https://market.yandex.ru',
    **_: object,
) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del server
        if request.path == '/final':
            return FixtureResponse(body=_html(clean_product_page()))
        return FixtureResponse(
            body=_html(navigating_page(f'{marketplace_origin}/final')),
        )

    return scenario


def _redirect_unsafe_scenario(**_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del server
        if request.path == '/attacker':
            return FixtureResponse(body=_html(attacker_page()))
        return FixtureResponse(
            body=_html(navigating_page(UNSAFE_REDIRECT_URL)),
        )

    return scenario


def _oversized_scenario(filler: int = 400_000, **_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del request, server
        return FixtureResponse(
            body=_html(clean_product_page(filler=filler)),
        )

    return scenario


def _slow_scenario(delay_sec: float = 3.0, **_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del request, server
        return FixtureResponse(
            body=_html(clean_product_page()),
            delay_sec=delay_sec,
        )

    return scenario


def _flaky_scenario(failures: int = 1, **_: object) -> Scenario:
    def scenario(
        request: FixtureRequest,
        server: FixtureServer,
    ) -> FixtureResponse:
        del request
        if server.count() < failures:
            return FixtureResponse(
                status=503,
                body=_html('<html><body>try later</body></html>'),
            )
        return FixtureResponse(body=_html(clean_product_page()))

    return scenario


_SCENARIOS: dict[str, Callable[..., Scenario]] = {
    'clean': _clean_scenario,
    'valid-empty': _valid_empty_scenario,
    'challenge-then-result': _challenge_then_result_scenario,
    'rate-limit': _rate_limit_scenario,
    'transport-error': _transport_error_scenario,
    'redirect-safe': _redirect_safe_scenario,
    'redirect-unsafe': _redirect_unsafe_scenario,
    'oversized': _oversized_scenario,
    'slow': _slow_scenario,
    'flaky': _flaky_scenario,
}


SCENARIO_NAMES: tuple[str, ...] = tuple(sorted(_SCENARIOS))


__all__ = (
    'FixtureRequest',
    'FixtureResponse',
    'FixtureServer',
    'SCENARIO_NAMES',
    'UNSAFE_REDIRECT_HOST',
    'UNSAFE_REDIRECT_URL',
    'attacker_page',
    'clean_product_page',
    'empty_page',
    'fixture_server',
    'navigating_page',
    'wall_page',
)
