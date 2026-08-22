"""Structural browser contracts shared by source and CAPTCHA adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol


EventHandler = Callable[[Any], Awaitable[None] | None]


class FrameLike(Protocol):
    """The main-frame surface needed for redirect validation."""

    @property
    def url(self) -> str:
        """Return the current frame URL."""


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

    def is_closed(self) -> bool:
        """Return whether the task page is closed."""

    def on(self, event: str, handler: EventHandler) -> None:
        """Register a Playwright-style event handler."""

    async def close(self) -> None:
        """Close the task page."""

    async def content(self) -> str:
        """Return serialized page HTML."""

    async def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript in the page."""

    async def title(self) -> str:
        """Return the document title."""

    async def wait_for_timeout(self, timeout: float) -> None:
        """Wait inside the browser runtime."""
