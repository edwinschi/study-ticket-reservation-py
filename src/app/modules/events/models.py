from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TicketType(TimestampMixin, Base):
    __tablename__ = "ticket_types"
    __table_args__ = (
        CheckConstraint(
            "total_quantity >= 0",
            name="total_quantity_non_negative",
        ),
        CheckConstraint(
            "sold_quantity >= 0",
            name="sold_quantity_non_negative",
        ),
        CheckConstraint(
            "reserved_quantity >= 0",
            name="reserved_quantity_non_negative",
        ),
        CheckConstraint(
            "sold_quantity + reserved_quantity <= total_quantity",
            name="allocated_quantity_within_total",
        ),
        Index("ix_ticket_types_event_id", "event_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    reserved_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )


class Seat(TimestampMixin, Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "section",
            "row_name",
            "seat_number",
            name="uq_seats_event_id_section_row_name_seat_number",
        ),
        Index("ix_seats_event_id", "event_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(String(100), nullable=False)
    row_name: Mapped[str] = mapped_column(String(50), nullable=False)
    seat_number: Mapped[str] = mapped_column(String(50), nullable=False)
