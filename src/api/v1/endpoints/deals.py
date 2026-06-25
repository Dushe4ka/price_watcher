"""Deal channel API endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.user import current_superuser
from src.crud.posted_deal import posted_deal_crud
from src.database.db import get_async_session
from src.models.user import User
from src.schemas.deal import DealRunStats, PostedDealRead
from src.services.categories_loader import load_categories_config
from src.services.deal_pipeline import DealPipeline

router = APIRouter(prefix='/deals', tags=['deals'])


@router.get(
    '/posted',
    response_model=list[PostedDealRead],
    status_code=status.HTTP_200_OK,
)
async def list_posted_deals(
    limit: int = 50,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_superuser),
) -> list[PostedDealRead]:
    deals = await posted_deal_crud.get_recent(session, limit=limit)
    return deals


@router.get('/categories')
async def list_monitored_categories(
    user: User = Depends(current_superuser),
) -> dict:
    config = load_categories_config()
    return config.model_dump()


@router.post(
    '/run',
    response_model=DealRunStats,
    status_code=status.HTTP_200_OK,
)
async def run_deals_manually(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_superuser),
) -> DealRunStats:
    pipeline = DealPipeline(bot=None)
    return await pipeline.run(session)
