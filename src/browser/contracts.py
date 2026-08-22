"""Structural browser contracts shared by source and CAPTCHA adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol


EventHandler = Callable[[Any], Awaitable[None] | None]


class LocatorLike(Protocol):
    """Locator surface for an element selected from an owned Frame."""

    async def click(self, *, timeout: float) -> None:
        """Click the target within a bounded Playwright timeout."""


class FrameLike(Protocol):
    """Frame surface needed for redirects and provider-owned controls."""

    @property
    def url(self) -> str:
        """Return the current frame URL."""

    def locator(self, selector: str) -> LocatorLike:
        """Create a locator owned by this exact frame."""


class NavigationResponseLike(Protocol):
    """Response metadata returned by a main-frame navigation."""

    @property
    def status(self) -> int:
        """Return the final HTTP response status."""


class BrowserContextLike(Protocol):
    """Persistent browser context boundary used by the lease manager."""

    @property
    def pages(self) -> Sequence[PageLike]:
        """Return pages currently owned by the context."""

    async def new_page(self) -> PageLike:
        """Create a task-scoped page."""

    async def close(self) -> None:
        """Close the persistent context and its browser process."""


class PageLike(Protocol):
    """Small Playwright-compatible page surface used across adapters."""

    @property
    def url(self) -> str:
        """Return the current main-frame URL."""

    @property
    def main_frame(self) -> FrameLike:
        """Return the page main frame."""

    @property
    def frames(self) -> Sequence[FrameLike]:
        """Return every frame owned by this exact page."""

    def is_closed(self) -> bool:
        """Return whether the task page is closed."""

    def on(self, event: str, handler: EventHandler) -> None:
        """Register a Playwright-style event handler."""

    async def close(self) -> None:
        """Close the task page."""

    async def content(self) -> str:
        """Return serialized page HTML."""

    async def goto(
        self,
        url: str,
        *,
        wait_until: str,
        timeout: float,
    ) -> NavigationResponseLike | None:
        """Navigate this page with an explicit Playwright timeout."""

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript in the page."""

    async def title(self) -> str:
        """Return the document title."""

    async def wait_for_timeout(self, timeout: float) -> None:
        """Wait inside the browser runtime."""
