import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db import models as domain_models
from app.db.session import async_session_factory, close_database
from app.modules.reservations.repository import ReservationRepository
from app.modules.reservations.service import ReservationLifecycleService

logger = logging.getLogger(__name__)
_ = domain_models


async def run_expiration_worker() -> None:
    """
    Run the local reservation expiration loop.

    The worker intentionally creates a fresh AsyncSession for each batch. That keeps
    transactions short and prevents one failed batch from leaving long-lived transaction state
    around the next iteration.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    service = ReservationLifecycleService(ReservationRepository())

    try:
        while True:
            await asyncio.sleep(settings.expiration_worker_interval_seconds)
            try:
                # Each batch uses its own session/transaction boundary. The service locks rows
                # with FOR UPDATE SKIP LOCKED, so multiple worker processes can run safely.
                async with async_session_factory() as session:
                    processed = await service.expire_batch(
                        session,
                        batch_size=settings.expiration_worker_batch_size,
                    )
                if processed:
                    logger.info("Expired %s reservations", processed)
            except Exception:
                # A worker failure should be visible in logs but should not stop future
                # expiration attempts in the local lab environment.
                logger.exception("Reservation expiration batch failed")
    finally:
        await close_database()


def main() -> None:
    asyncio.run(run_expiration_worker())


if __name__ == "__main__":
    main()
