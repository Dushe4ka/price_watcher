import re
from dataclasses import dataclass

WB_URL = re.compile(
    r'(?:wildberries\.ru|wb\.ru)/catalog/(\d+)',
    re.IGNORECASE,
)
OZON_URL = re.compile(
    r'ozon\.ru/(?:product/[^/\s?#]+-(\d+)|product/(\d+))',
    re.IGNORECASE,
)
YANDEX_URL = re.compile(
    r'market\.yandex\.ru/card/[^/\s?#]+/(\d+)',
    re.IGNORECASE,
)
WB_ARTICLE = re.compile(r'^\d{5,12}$')

MARKETPLACE_URL_HINTS = (
    'wildberries.ru',
    'wb.ru',
    'ozon.ru',
    'market.yandex',
)


@dataclass(frozen=True, slots=True)
class ParsedTrackInput:
    marketplace: str
    article: str


def is_track_url(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in MARKETPLACE_URL_HINTS)


def parse_track_input(text: str) -> ParsedTrackInput | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    match = WB_URL.search(cleaned)
    if match:
        return ParsedTrackInput('wildberries', match.group(1))

    match = OZON_URL.search(cleaned)
    if match:
        article = match.group(1) or match.group(2)
        return ParsedTrackInput('ozon', article)

    match = YANDEX_URL.search(cleaned)
    if match:
        return ParsedTrackInput('yandex_market', match.group(1))

    if WB_ARTICLE.match(cleaned):
        return ParsedTrackInput('wildberries', cleaned)

    return None
