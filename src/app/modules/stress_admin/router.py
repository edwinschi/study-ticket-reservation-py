from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.db.session import get_db_session
from app.modules.stress_admin.schemas import (
    StressConsistencyResponse,
    StressResetResponse,
    StressSeedResponse,
)
from app.modules.stress_admin.service import StressAdminService

router = APIRouter(prefix="/v1/admin/stress", tags=["stress-admin"])

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]


def ensure_local_environment() -> None:
    if get_settings().app_env == "production":
        raise NotFoundError(
            "Not found",
            code="NOT_FOUND",
        )


@router.post(
    "/seed",
    response_model=StressSeedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(ensure_local_environment)],
)
async def seed_stress_data(db_session: DatabaseSession) -> StressSeedResponse:
    return await StressAdminService().seed(db_session)


@router.post(
    "/reset",
    response_model=StressResetResponse,
    dependencies=[Depends(ensure_local_environment)],
)
async def reset_stress_data(db_session: DatabaseSession) -> StressResetResponse:
    return await StressAdminService().reset(db_session)


@router.get(
    "/assert-consistency",
    response_model=StressConsistencyResponse,
    dependencies=[Depends(ensure_local_environment)],
)
async def assert_stress_consistency(
    db_session: DatabaseSession,
) -> StressConsistencyResponse:
    return await StressAdminService().assert_consistency(db_session)
