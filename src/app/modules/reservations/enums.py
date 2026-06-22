from enum import Enum, StrEnum


class ReservationStatus(StrEnum):
    RESERVED = "reserved"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReservationType(StrEnum):
    QUANTITY = "quantity"
    SEATS = "seats"


def enum_values(enum_class: type[Enum]) -> list[str]:
    return [str(member.value) for member in enum_class]
