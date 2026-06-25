from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.posted_deal import PostedDeal
from src.schemas.deal import PostedDealCreate


class PostedDealCRUD:
    async def exists(
        self,
        marketplace: str,
        external_id: str,
        session: AsyncSession,
    ) -> bool:
        result = await session.execute(
            select(PostedDeal.id).where(
                PostedDeal.marketplace == marketplace,
                PostedDeal.external_id == external_id,
            )
        )
        return result.scalar() is not None

    async def create(
        self,
        data: PostedDealCreate,
        session: AsyncSession,
    ) -> PostedDeal:
        deal = PostedDeal(**data.model_dump())
        session.add(deal)
        await session.commit()
        await session.refresh(deal)
        return deal

    async def count_all(self, session: AsyncSession) -> int:
        result = await session.execute(select(PostedDeal.id))
        return len(result.scalars().all())

    async def get_recent(
        self,
        session: AsyncSession,
        limit: int = 20,
    ) -> list[PostedDeal]:
        result = await session.execute(
            select(PostedDeal)
            .order_by(PostedDeal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


posted_deal_crud = PostedDealCRUD()
