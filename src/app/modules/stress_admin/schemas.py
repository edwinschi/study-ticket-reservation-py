from uuid import UUID

from pydantic import BaseModel

ConsistencyDataValue = str | int | bool | None


class StressSeedResponse(BaseModel):
    event_id: UUID
    ticket_type_id: UUID
    seat_ids: list[UUID]


class StressResetResponse(BaseModel):
    events_deleted: int


class StressConsistencyChecks(BaseModel):
    ticket_quantity_not_oversold: bool
    ticket_quantity_not_negative: bool
    no_duplicate_active_seats: bool
    no_orphan_reservation_items: bool
    no_orphan_reservation_seats: bool
    no_stale_active_reservations: bool


class StressConsistencyDetail(BaseModel):
    check: str
    message: str
    data: dict[str, ConsistencyDataValue]


class StressConsistencyResponse(BaseModel):
    ok: bool
    checks: StressConsistencyChecks
    details: list[StressConsistencyDetail]
