"""Safe persistent browser contexts for marketplace fallbacks."""

from src.browser.allowlist import (
    CategoryUrlResolutionRequired,
    UnsafeMarketplaceUrl,
    build_marketplace_url,
    validate_main_frame_url,
)
from src.browser.contracts import BrowserContextLike, PageLike
from src.browser.profiles import (
    BrowserSessionCloseError,
    BrowserSessionManager,
    ProfileLock,
)

__all__ = [
    'BrowserContextLike',
    'BrowserSessionCloseError',
    'BrowserSessionManager',
    'CategoryUrlResolutionRequired',
    'PageLike',
    'ProfileLock',
    'UnsafeMarketplaceUrl',
    'build_marketplace_url',
    'validate_main_frame_url',
]
