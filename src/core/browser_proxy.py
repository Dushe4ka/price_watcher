"""Shared helpers for every Playwright-backed marketplace client."""

from __future__ import annotations

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(
    navigator,
    'languages',
    { get: () => ['ru-RU', 'ru', 'en-US', 'en'] },
);
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

_CHROMIUM_RUNTIME_ARGS = (
    '--disable-blink-features=AutomationControlled',
)


def chromium_runtime_args() -> list[str]:
    """Return reviewed Chromium arguments without sandbox bypasses."""
    return list(_CHROMIUM_RUNTIME_ARGS)


def playwright_proxy_config(proxy: str) -> dict[str, str]:
    """Split a ``scheme://user:pass@host:port`` PROXY_LIST entry into the
    ``{server, username, password}`` shape Playwright expects — Chromium's
    proxy-server flag has no support for credentials embedded in the URL."""
    scheme, _, rest = proxy.partition('://')
    auth, _, host_port = rest.rpartition('@')
    if not auth:
        return {'server': proxy}
    username, _, password = auth.partition(':')
    return {
        'server': f'{scheme}://{host_port}',
        'username': username,
        'password': password,
    }
