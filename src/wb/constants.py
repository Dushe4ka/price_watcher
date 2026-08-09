from __future__ import annotations

WB_HOME_URL = 'https://www.wildberries.ru/'

WB_DESKTOP_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)


def build_product_url(product_id: str) -> str:
    return f'https://www.wildberries.ru/catalog/{product_id}/detail.aspx'


def build_search_url(query: str) -> str:
    from urllib.parse import quote
    return f'https://www.wildberries.ru/catalog/0/search.aspx?search={quote(query)}'


# Extracts one dict per product card from a category/search results page.
CATEGORY_CARDS_JS = """
() => Array.from(document.querySelectorAll('.product-card')).map(c => {
  const q = (sel) => c.querySelector(sel);
  const text = (el) => el ? el.textContent.trim() : null;
  const link = c.querySelector('a[aria-label]');
  const img = c.querySelector('img');
  return {
    nmId: c.getAttribute('data-nm-id'),
    title: link ? link.getAttribute('aria-label') : null,
    brand: text(q('.product-card__brand')),
    imageUrl: img ? (img.getAttribute('src') || img.getAttribute('data-src-pb')) : null,
    priceCurrent: text(q('.price__wrap ins')),
    priceOld: text(q('.price__wrap del')),
    ratingValue: text(q('.address-rate-mini')),
    reviewText: text(q('.product-card__count')),
  };
});
"""

# Extracts a single dict describing the product on a detail page.
DETAIL_PAGE_JS = """
() => {
  const q = (sel) => document.querySelector(sel);
  const text = (el) => el ? el.textContent.trim() : null;
  const img = q('.product-page img, [class*=photo] img, [class*=slide] img');
  return {
    priceCurrent: text(q('[class*=priceBlockFinalPrice]')),
    priceOld: text(q('[class*=priceBlockOldPrice]')),
    ratingValue: text(q('[class*=ratingNumber]')),
    reviewText: text(q('[class*=reviewCount]')),
    imageUrl: img ? img.src : null,
    pageTitle: document.title,
  };
}
"""
