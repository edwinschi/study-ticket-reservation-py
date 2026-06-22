from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.reservations.enums import ReservationStatus, ReservationType


class QuantityReservationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: UUID
    ticket_type_id: UUID
    quantity: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=255)


class QuantityReservationItemResponse(BaseModel):
    ticket_type_id: UUID
    quantity: int


class QuantityReservationResponse(BaseModel):
    reservation_id: UUID
    status: ReservationStatus
    reservation_type: ReservationType
    expires_at: datetime
    items: list[QuantityReservationItemResponse]


class SeatReservationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: UUID
    seat_ids: list[UUID] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_unique_seat_ids(self) -> "SeatReservationCreate":
        if len(set(self.seat_ids)) != len(self.seat_ids):
            raise ValueError("seat_ids must not contain duplicates")
        return self


class SeatReservationItemResponse(BaseModel):
    seat_id: UUID


class SeatReservationResponse(BaseModel):
    reservation_id: UUID
    status: ReservationStatus
    reservation_type: ReservationType
    expires_at: datetime
    seats: list[SeatReservationItemResponse]


class ReservationItemDetailResponse(BaseModel):
    ticket_type_id: UUID
    quantity: int
    status: ReservationStatus


class ReservationSeatDetailResponse(BaseModel):
    seat_id: UUID
    status: ReservationStatus


class ReservationDetailResponse(BaseModel):
    reservation_id: UUID
    event_id: UUID
    status: ReservationStatus
    reservation_type: ReservationType
    expires_at: datetime
    items: list[ReservationItemDetailResponse]
    seats: list[ReservationSeatDetailResponse]
