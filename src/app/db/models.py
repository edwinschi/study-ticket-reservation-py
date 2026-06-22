"""Import every model so Alembic can discover the complete metadata."""

from app.modules.events.models import Event as Event
from app.modules.events.models import Seat as Seat
from app.modules.events.models import TicketType as TicketType
from app.modules.reservations.models import Reservation as Reservation
from app.modules.reservations.models import ReservationItem as ReservationItem
from app.modules.reservations.models import ReservationSeat as ReservationSeat
from app.modules.sessions.models import VisitorSession as VisitorSession
from app.modules.users.models import User as User

__all__ = [
    "Event",
    "Reservation",
    "ReservationItem",
    "ReservationSeat",
    "Seat",
    "TicketType",
    "User",
    "VisitorSession",
]
