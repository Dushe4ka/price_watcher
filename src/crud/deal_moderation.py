from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.enums import ModerationStatus
from src.models.deal_moderation import DealModeration
from src.schemas.deal import DealModerationCreate


class DealModerationCRUD:
    async def create(
        self,
        session: AsyncSession,
        data: DealModerationCreate,
    ) -> DealModeration:
        payload = data.model_dump()
        payload['status'] = ModerationStatus(payload['status'])
        moderation = DealModeration(**payload)
        session.add(moderation)
        await session.commit()
        await session.refresh(moderation)
        return moderation

    async def get(self, session: AsyncSession, moderation_id: int) -> DealModeration | None:
        result = await session.execute(
            select(DealModeration).where(DealModeration.id == moderation_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        session: AsyncSession,
        moderation: DealModeration,
        status: ModerationStatus,
        decision_reason: str,
        channel_message_id: int | None = None,
        admin_message_id: int | None = None,
    ) -> DealModeration:
        moderation.status = status
        moderation.decision_reason = decision_reason
        moderation.resolved_at = datetime.now(timezone.utc)
        if channel_message_id is not None:
            moderation.channel_message_id = channel_message_id
        if admin_message_id is not None:
            moderation.admin_message_id = admin_message_id
        session.add(moderation)
        await session.commit()
        await session.refresh(moderation)
        return moderation

    async def has_pending_for_product(
        self,
        session: AsyncSession,
        marketplace: str,
        external_id: str,
    ) -> bool:
        result = await session.execute(
            select(DealModeration.id).where(
                DealModeration.marketplace == marketplace,
                DealModeration.external_id == external_id,
                DealModeration.status == ModerationStatus.PENDING,
            )
        )
        return result.scalar() is not None


deal_moderation_crud = DealModerationCRUD()
