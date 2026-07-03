from dataclasses import dataclass
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.core.config import settings
from src.database.enums import Marketplace
from src.parsers.base import ParsedProduct


MARKETPLACE_LABELS = {
    Marketplace.WILDBERRIES.value: 'Wildberries',
    Marketplace.OZON.value: 'Ozon',
    Marketplace.YANDEX_MARKET.value: 'Яндекс Маркет',
}

MARKETPLACE_ICONS = {
    Marketplace.WILDBERRIES.value: '🟣',
    Marketplace.OZON.value: '🔵',
    Marketplace.YANDEX_MARKET.value: '🟡',
}


def _format_price(value: Decimal | None) -> str:
    if value is None:
        return '—'
    formatted = f'{value:,.0f}'.replace(',', ' ')
    return f'{formatted} ₽'


@dataclass(frozen=True, slots=True)
class FormattedPost:
    text: str
    reply_markup: InlineKeyboardMarkup | None = None


def format_deal_post(
    product: ParsedProduct,
    marketplace: str,
    hashtag: str,
    *,
    discount_percent: int | None = None,
    show_average_price_note: bool = False,
    average_price: Decimal | None = None,
    database_discount_percent: int | None = None,
    show_market_note: bool = False,
    market_min_price: Decimal | None = None,
    market_discount_percent: int | None = None,
) -> FormattedPost:
    marketplace_label = MARKETPLACE_LABELS.get(marketplace, marketplace)
    mp_icon = MARKETPLACE_ICONS.get(marketplace, '🏷')
    discount = discount_percent if discount_percent is not None else (
        product.discount_percent or 0
    )

    lines = [
        f'🔥 <b>Скидка {discount}%</b> | {mp_icon} {marketplace_label}',
    ]

    if show_market_note and market_discount_percent is not None:
        lines.append(
            f'🛒 Дешевле рынка на {market_discount_percent}% '
            f'(мин: {_format_price(market_min_price)})'
        )
    if show_average_price_note and database_discount_percent is not None:
        lines.append(
            f'📊 По средней за '
            f'{settings.price_history_retention_days} дн.: '
            f'-{database_discount_percent}%'
        )

    lines.extend(['', f'<b>{product.title}</b>'])

    if show_average_price_note and average_price is not None:
        lines.append(
            f'Средняя: {_format_price(average_price)} → '
            f'<b>{_format_price(product.price)}</b>'
        )
    elif product.original_price and product.original_price > product.price:
        lines.append(
            f'<s>{_format_price(product.original_price)}</s> → '
            f'<b>{_format_price(product.price)}</b>'
        )
    else:
        lines.append(f'<b>{_format_price(product.price)}</b>')

    lines.extend([
        '',
        f'#{hashtag} #{marketplace} #скидки',
    ])

    reply_markup = None
    if product.product_url:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f'🛒 Перейти к товару — {marketplace_label}',
                url=product.product_url,
            )],
        ])

    return FormattedPost(text='\n'.join(lines), reply_markup=reply_markup)


def format_moderation_request(
    product: ParsedProduct,
    marketplace: str,
    hashtag: str,
    *,
    parser_discount: int | None,
    database_discount: int | None,
    average_price: Decimal | None,
    reason: str,
    market_min_price: Decimal | None = None,
    market_discount_percent: int | None = None,
) -> str:
    marketplace_label = MARKETPLACE_LABELS.get(marketplace, marketplace)
    lines = [
        '⚠️ <b>Требуется модерация</b>',
        f'{marketplace_label} | #{hashtag}',
        '',
        product.title,
        f'Цена: <b>{_format_price(product.price)}</b>',
    ]
    if product.original_price:
        lines.append(
            f'Цена парсера (было): {_format_price(product.original_price)}'
        )
    if average_price is not None:
        lines.append(
            f'Средняя за {settings.price_history_retention_days} дней: '
            f'{_format_price(average_price)}'
        )
    if parser_discount is not None:
        lines.append(f'Скидка по парсеру: {parser_discount}%')
    if database_discount is not None:
        lines.append(f'Скидка по базе: {database_discount}%')
    else:
        lines.append('Скидка по базе: недостаточно данных')
    if market_min_price is not None:
        lines.append(f'Мин. цена на других площадках: {_format_price(market_min_price)}')
    if market_discount_percent is not None:
        lines.append(f'Дешевле рынка: {market_discount_percent}%')
    lines.extend(['', f'Причина: {reason}', '', 'Опубликовать в канал?'])
    return '\n'.join(lines)
