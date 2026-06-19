from .dashboard import router as dashboard_router
from .keys import router as keys_router
from .users import router as users_router
from .repository import router as repository_router
from .config_admin import router as config_router
from .usage import router as usage_router

__all__ = [
    "dashboard_router",
    "keys_router",
    "users_router",
    "repository_router",
    "config_router",
    "usage_router",
]
