"""Virtual Office authority boundary.  It exposes no HTTP surface."""

from .authority import authorize_office, authorize_office_in_cursor
from .capabilities import OfficeOperation

__all__ = ("OfficeOperation", "authorize_office", "authorize_office_in_cursor")
