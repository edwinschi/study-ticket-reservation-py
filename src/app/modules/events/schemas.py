from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class EventCreate(InputModel):
    name: str = Field(min_length=1, max_length=255)
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def validate_schedule(self) -> "EventCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    updated_at: datetime


class TicketTypeCreate(InputModel):
    name: str = Field(min_length=1, max_length=255)
    total_quantity: int = Field(ge=0)


class TicketTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    name: str
    total_quantity: int
    sold_quantity: int
    reserved_quantity: int
    created_at: datetime
    updated_at: datetime


class SeatCreate(InputModel):
    section: str = Field(min_length=1, max_length=100)
    row_name: str = Field(min_length=1, max_length=50)
    seat_number: str = Field(min_length=1, max_length=50)


class SeatBatchCreate(BaseModel):
    seats: list[SeatCreate] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def validate_unique_seats(self) -> "SeatBatchCreate":
        seat_keys = {(seat.section, seat.row_name, seat.seat_number) for seat in self.seats}
        if len(seat_keys) != len(self.seats):
            raise ValueError("seat list contains duplicate positions")
        return self


class SeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    section: str
    row_name: str
    seat_number: str
    created_at: datetime
    updated_at: datetime


class SeatBatchResponse(BaseModel):
    seats: list[SeatResponse]


class SeatInventoryStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    CONFIRMED = "confirmed"


class TicketTypeInventoryResponse(BaseModel):
    id: UUID
    name: str
    total: int
    sold: int
    reserved: int
    available: int


class SeatInventoryResponse(BaseModel):
    id: UUID
    section: str
    row_name: str
    seat_number: str
    status: SeatInventoryStatus


class EventInventoryResponse(BaseModel):
    event_id: UUID
    ticket_types: list[TicketTypeInventoryResponse]
    seats: list[SeatInventoryResponse]
