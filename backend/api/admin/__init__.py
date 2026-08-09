from .dashboard import router as dashboard_router
from .keys import router as keys_router
from .credentials import router as credentials_router
from .users import router as users_router
from .repository import router as repository_router
from .config_admin import router as config_router
from .usage import router as usage_router
from .facet_models import router as facet_models_router
from .models import router as models_router
from .facet_bindings import router as facet_bindings_router

__all__ = [
    "dashboard_router",
    "keys_router",
    "credentials_router",
    "users_router",
    "repository_router",
    "config_router",
    "usage_router",
    "facet_models_router",
    "models_router",
    "facet_bindings_router",
]
