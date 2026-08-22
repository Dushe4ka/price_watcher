from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from src.captcha.models import ChallengeResolution


@dataclass(frozen=True, slots=True)
class FakeNavigationResponse:
    status: int = 200


class FakePage:
    def __init__(
        self,
        *,
        html: str,
        evaluation: object = None,
        status: int = 200,
        redirect_url: str | None = None,
    ) -> None:
        self.url = 'about:blank'
        self.html = html
        self.evaluation = evaluation
        self.status = status
        self.redirect_url = redirect_url
        self.closed = False
        self.goto_urls: list[str] = []
        self.goto_timeouts: list[float] = []
        self.content_pages: list[FakePage] = []
        self.evaluation_pages: list[FakePage] = []
        self.expressions: list[str] = []
        self.handlers: dict[str, list[Callable[[Any], Any]]] = {}

    @property
    def main_frame(self) -> object:
        return self

    @property
    def frames(self) -> tuple[()]:
        return ()

    def is_closed(self) -> bool:
        return self.closed

    def on(self, event: str, handler: Callable[[Any], Any]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def close(self) -> None:
        self.closed = True

    async def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: float,
    ) -> FakeNavigationResponse:
        del wait_until
        self.goto_urls.append(url)
        self.goto_timeouts.append(timeout)
        self.url = self.redirect_url or url
        return FakeNavigationResponse(self.status)

    async def content(self) -> str:
        self.content_pages.append(self)
        return self.html

    async def evaluate(self, expression: str) -> object:
        self.evaluation_pages.append(self)
        self.expressions.append(expression)
        return self.evaluation

    async def title(self) -> str:
        return 'Synthetic marketplace page'

    async def wait_for_timeout(self, timeout: float) -> None:
        await asyncio.sleep(timeout / 1000)


class HangingContentPage(FakePage):
    def __init__(self, *, html: str) -> None:
        super().__init__(html=html)
        self.late_mutations = 0

    async def content(self) -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            if not self.closed:
                self.late_mutations += 1
            raise


class BrokenContentPage(FakePage):
    async def content(self) -> str:
        raise RuntimeError('synthetic-sensitive-browser-body')


class RedirectOnContentPage(FakePage):
    def __init__(
        self,
        *,
        html: str,
        redirect_after_call: int,
    ) -> None:
        super().__init__(html=html)
        self.redirect_after_call = redirect_after_call
        self.content_calls = 0

    async def content(self) -> str:
        self.content_calls += 1
        self.content_pages.append(self)
        if self.content_calls >= self.redirect_after_call:
            self.url = 'https://attacker.invalid/late-redirect'
        return self.html


class BrokenEvaluationPage(FakePage):
    async def evaluate(self, expression: str) -> object:
        del expression
        raise RuntimeError('synthetic DOM extraction drift')


class FakeManager:
    def __init__(
        self,
        page: FakePage,
        *,
        lease_error: Exception | None = None,
    ) -> None:
        self.page = page
        self.lease_error = lease_error
        self.marketplaces: list[str] = []

    @asynccontextmanager
    async def lease(self, marketplace: str) -> AsyncIterator[FakePage]:
        self.marketplaces.append(marketplace)
        if self.lease_error is not None:
            raise self.lease_error
        try:
            yield self.page
        finally:
            if not self.page.is_closed():
                await self.page.close()


class FakeCoordinator:
    def __init__(
        self,
        *resolutions: ChallengeResolution,
    ) -> None:
        self._resolutions = list(
            resolutions
            or (
                ChallengeResolution.NO_CHALLENGE,
                ChallengeResolution.NO_CHALLENGE,
            )
        )
        self.pages: list[FakePage] = []
        self.deadlines: list[object] = []

    async def resolve(
        self,
        page: FakePage,
        *,
        deadline: object,
    ) -> ChallengeResolution:
        self.pages.append(page)
        self.deadlines.append(deadline)
        if self._resolutions:
            return self._resolutions.pop(0)
        return ChallengeResolution.NO_CHALLENGE
