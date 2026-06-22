"""Create ticket reservation domain tables.

Revision ID: 20260622_0001
Revises:
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260622_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

reservation_status_enum = postgresql.ENUM(
    "reserved",
    "confirmed",
    "cancelled",
    "expired",
    name="reservation_status",
    create_type=False,
)
reservation_type_enum = postgresql.ENUM(
    "quantity",
    "seats",
    name="reservation_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    reservation_status_enum.create(bind, checkfirst=True)
    reservation_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )

    op.create_table(
        "visitor_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("anonymous_token_hash", sa.String(length=128), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_visitor_sessions_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_visitor_sessions"),
        sa.UniqueConstraint(
            "anonymous_token_hash",
            name="uq_visitor_sessions_anonymous_token_hash",
        ),
    )

    op.create_table(
        "ticket_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("total_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "sold_quantity",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "reserved_quantity",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "total_quantity >= 0",
            name=op.f("ck_ticket_types_total_quantity_non_negative"),
        ),
        sa.CheckConstraint(
            "sold_quantity >= 0",
            name=op.f("ck_ticket_types_sold_quantity_non_negative"),
        ),
        sa.CheckConstraint(
            "reserved_quantity >= 0",
            name=op.f("ck_ticket_types_reserved_quantity_non_negative"),
        ),
        sa.CheckConstraint(
            "sold_quantity + reserved_quantity <= total_quantity",
            name=op.f("ck_ticket_types_allocated_quantity_within_total"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_ticket_types_event_id_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_types"),
    )
    op.create_index("ix_ticket_types_event_id", "ticket_types", ["event_id"])

    op.create_table(
        "seats",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section", sa.String(length=100), nullable=False),
        sa.Column("row_name", sa.String(length=50), nullable=False),
        sa.Column("seat_number", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_seats_event_id_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_seats"),
        sa.UniqueConstraint(
            "event_id",
            "section",
            "row_name",
            "seat_number",
            name="uq_seats_event_id_section_row_name_seat_number",
        ),
    )
    op.create_index("ix_seats_event_id", "seats", ["event_id"])

    op.create_table(
        "reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visitor_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", reservation_status_enum, nullable=False),
        sa.Column("reservation_type", reservation_type_enum, nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_reservations_event_id_events",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_reservations_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["visitor_session_id"],
            ["visitor_sessions.id"],
            name="fk_reservations_visitor_session_id_visitor_sessions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reservations"),
        sa.UniqueConstraint(
            "visitor_session_id",
            "idempotency_key",
            name="uq_reservations_visitor_session_id_idempotency_key",
        ),
    )
    op.create_index(
        "ix_reservations_visitor_session_id",
        "reservations",
        ["visitor_session_id"],
    )
    op.create_index("ix_reservations_user_id", "reservations", ["user_id"])
    op.create_index(
        "ix_reservations_status_expires_at",
        "reservations",
        ["status", "expires_at"],
    )

    op.create_table(
        "reservation_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticket_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", reservation_status_enum, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_reservation_items_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name="fk_reservation_items_reservation_id_reservations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_type_id"],
            ["ticket_types.id"],
            name="fk_reservation_items_ticket_type_id_ticket_types",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reservation_items"),
    )
    op.create_index(
        "ix_reservation_items_ticket_type_id_status",
        "reservation_items",
        ["ticket_type_id", "status"],
    )

    op.create_table(
        "reservation_seats",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seat_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", reservation_status_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
            name="fk_reservation_seats_reservation_id_reservations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seat_id"],
            ["seats.id"],
            name="fk_reservation_seats_seat_id_seats",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reservation_seats"),
    )
    op.create_index(
        "ix_reservation_seats_seat_id_status",
        "reservation_seats",
        ["seat_id", "status"],
    )
    op.create_index(
        "uq_active_reservation_seat",
        "reservation_seats",
        ["seat_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('reserved', 'confirmed')"),
    )


def downgrade() -> None:
    op.drop_table("reservation_seats")
    op.drop_table("reservation_items")
    op.drop_table("reservations")
    op.drop_table("seats")
    op.drop_table("ticket_types")
    op.drop_table("visitor_sessions")
    op.drop_table("events")
    op.drop_table("users")

    bind = op.get_bind()
    reservation_type_enum.drop(bind, checkfirst=True)
    reservation_status_enum.drop(bind, checkfirst=True)
