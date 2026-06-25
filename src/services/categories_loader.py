from pathlib import Path

import yaml
from pydantic import ValidationError

from src.core.config import settings
from src.schemas.deal import CategoriesConfig, CategoryConfig


def load_categories_config(
    path: str | None = None,
) -> CategoriesConfig:
    config_path = Path(path or settings.categories_config_path)
    if not config_path.is_absolute():
        for base in (Path.cwd(), Path(__file__).resolve().parents[2]):
            candidate = base / config_path
            if candidate.exists():
                config_path = candidate
                break
    if not config_path.exists():
        return CategoriesConfig(categories=[])
    with config_path.open(encoding='utf-8') as file:
        raw = yaml.safe_load(file) or {}
    return CategoriesConfig.model_validate(raw)


def get_category_by_slug(slug: str) -> CategoryConfig | None:
    config = load_categories_config()
    for category in config.categories:
        if category.slug == slug:
            return category
    return None
