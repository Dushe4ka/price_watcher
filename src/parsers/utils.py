from __future__ import annotations

import asyncio
import functools
import itertools
import logging
import random
from typing import Any, Callable, Coroutine, TypeVar

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

USER_AGENTS: list[str] = [
    (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    (
        'Mozilla/5.0 (Linux; Android 14; SM-S918B) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Mobile Safari/537.36'
    ),
    (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) '
        'Gecko/20100101 Firefox/125.0'
    ),
]


def get_random_ua() -> str:
    return random.choice(USER_AGENTS)


_proxy_cycle: itertools.cycle[str] | None = None


def get_proxy() -> str | None:
    global _proxy_cycle
    proxies = settings.proxies
    if not proxies:
        return None
    if _proxy_cycle is None:
        _proxy_cycle = itertools.cycle(proxies)
    return next(_proxy_cycle)


def create_http_client(**kwargs: Any) -> httpx.AsyncClient:
    proxy = get_proxy()
    headers = kwargs.pop('headers', {})
    headers.setdefault('User-Agent', get_random_ua())
    headers.setdefault('Accept', 'application/json, text/html, */*')
    headers.setdefault(
        'Accept-Language',
        'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    )
    timeout = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
    client_kwargs: dict[str, Any] = {
        'headers': headers,
        'timeout': timeout,
        'follow_redirects': True,
        **kwargs,
    }
    if proxy:
        client_kwargs['proxy'] = proxy
    return httpx.AsyncClient(**client_kwargs)


class ParserError(Exception):
    """Base exception for parser errors."""


class BlockedError(ParserError):
    pass


class NotFoundError(ParserError):
    pass


class ParsingError(ParserError):
    pass


T = TypeVar('T')
_RETRY_DELAYS: tuple[int, ...] = (2, 5, 15)


def retry_request(
    fn: Callable[..., Coroutine[Any, Any, T]],
) -> Callable[..., Coroutine[Any, Any, T]]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        last_exc: BaseException | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, 0), start=1):
            try:
                return await fn(*args, **kwargs)
            except NotFoundError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt <= len(_RETRY_DELAYS):
                    logger.warning(
                        'Retry %s attempt %s failed: %s',
                        fn.__qualname__,
                        attempt,
                        exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        'All retries failed for %s: %s',
                        fn.__qualname__,
                        exc,
                    )
                    raise last_exc  # type: ignore[misc]
        raise RuntimeError('unreachable')

    return wrapper
