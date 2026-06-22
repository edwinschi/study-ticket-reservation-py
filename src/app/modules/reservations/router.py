from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import get_db_session
from app.modules.reservations.repository import ReservationRepository
from app.modules.reservations.schemas import (
    QuantityReservationCreate,
    QuantityReservationResponse,
    ReservationDetailResponse,
    SeatReservationCreate,
    SeatReservationResponse,
)
from app.modules.reservations.service import (
    QuantityReservationConflictError,
    QuantityReservationService,
    ReservationInventoryConflictError,
    ReservationLifecycleService,
    ReservationNotFoundError,
    ReservationTransitionConflictError,
    SeatReservationConflictError,
    SeatReservationService,
    SeatsNotFoundError,
)
from app.modules.sessions.dependencies import get_current_session
from app.modules.sessions.models import VisitorSession

router = APIRouter(prefix="/v1/reservations", tags=["reservations"])

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentVisitorSession = Annotated[VisitorSession, Depends(get_current_session)]


def get_quantity_reservation_service() -> QuantityReservationService:
    return QuantityReservationService(ReservationRepository(), get_settings())


def get_seat_reservation_service() -> SeatReservationService:
    return SeatReservationService(ReservationRepository(), get_settings())


def get_reservation_lifecycle_service() -> ReservationLifecycleService:
    return ReservationLifecycleService(ReservationRepository())


@router.post(
    "/quantity",
    response_model=QuantityReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reserve_quantity(
    payload: QuantityReservationCreate,
    db_session: DatabaseSession,
    visitor_session: CurrentVisitorSession,
) -> QuantityReservationResponse:
    """
    Create a quantity reservation for the current visitor session.

    The endpoint remains thin on purpose: HTTP validation and error mapping live here, while the
    service owns the transaction and the atomic stock update.
    """
    try:
        return await get_quantity_reservation_service().reserve(
            db_session,
            visitor_session=visitor_session,
            event_id=payload.event_id,
            ticket_type_id=payload.ticket_type_id,
            quantity=payload.quantity,
            idempotency_key=payload.idempotency_key,
        )
    except QuantityReservationConflictError as exc:
        # Exhausted stock is a normal business conflict under load, not an unexpected server
        # failure. The standardized exception handler will include the request id.
        raise ConflictError(
            "Not enough stock available",
            code="INSUFFICIENT_STOCK",
        ) from exc


@router.post(
    "/seats",
    response_model=SeatReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reserve_seats(
    payload: SeatReservationCreate,
    db_session: DatabaseSession,
    visitor_session: CurrentVisitorSession,
) -> SeatReservationResponse:
    """
    Create a seat reservation for the current visitor session.

    The service validates seat ownership, locks seats in deterministic order, and relies on the
    partial unique index to reject duplicate active occupancy.
    """
    try:
        return await get_seat_reservation_service().reserve(
            db_session,
            visitor_session=visitor_session,
            event_id=payload.event_id,
            seat_ids=payload.seat_ids,
            idempotency_key=payload.idempotency_key,
        )
    except SeatsNotFoundError as exc:
        raise NotFoundError(
            "One or more seats do not exist for this event",
            code="SEATS_NOT_FOUND",
        ) from exc
    except SeatReservationConflictError as exc:
        # Another request already holds or confirmed at least one seat. Returning 409 keeps
        # expected contention visible to clients and stress tests without treating it as a 500.
        raise ConflictError(
            "One or more seats are already reserved or confirmed",
            code="SEAT_UNAVAILABLE",
        ) from exc


@router.get("/{reservation_id}", response_model=ReservationDetailResponse)
async def get_reservation(
    reservation_id: UUID,
    db_session: DatabaseSession,
    visitor_session: CurrentVisitorSession,
) -> ReservationDetailResponse:
    try:
        return await get_reservation_lifecycle_service().get(
            db_session,
            reservation_id=reservation_id,
            visitor_session=visitor_session,
        )
    except ReservationNotFoundError as exc:
        raise NotFoundError(
            "Reservation not found",
            code="RESERVATION_NOT_FOUND",
        ) from exc


@router.post(
    "/{reservation_id}/cancel",
    response_model=ReservationDetailResponse,
)
async def cancel_reservation(
    reservation_id: UUID,
    db_session: DatabaseSession,
    visitor_session: CurrentVisitorSession,
) -> ReservationDetailResponse:
    """Cancel an owned reservation, releasing inventory when the transition is valid."""
    try:
        return await get_reservation_lifecycle_service().cancel(
            db_session,
            reservation_id=reservation_id,
            visitor_session=visitor_session,
        )
    except ReservationNotFoundError as exc:
        raise NotFoundError(
            "Reservation not found",
            code="RESERVATION_NOT_FOUND",
        ) from exc
    except (
        ReservationInventoryConflictError,
        ReservationTransitionConflictError,
    ) as exc:
        raise ConflictError(
            "Reservation cannot be cancelled from its current state",
            code="RESERVATION_NOT_CANCELLABLE",
        ) from exc


@router.post(
    "/{reservation_id}/confirm",
    response_model=ReservationDetailResponse,
)
async def confirm_reservation(
    reservation_id: UUID,
    db_session: DatabaseSession,
    visitor_session: CurrentVisitorSession,
) -> ReservationDetailResponse:
    """Confirm an owned reservation, simulating a successful purchase without payment."""
    try:
        return await get_reservation_lifecycle_service().confirm(
            db_session,
            reservation_id=reservation_id,
            visitor_session=visitor_session,
        )
    except ReservationNotFoundError as exc:
        raise NotFoundError(
            "Reservation not found",
            code="RESERVATION_NOT_FOUND",
        ) from exc
    except (
        ReservationInventoryConflictError,
        ReservationTransitionConflictError,
    ) as exc:
        raise ConflictError(
            "Reservation cannot be confirmed from its current state",
            code="RESERVATION_NOT_CONFIRMABLE",
        ) from exc
