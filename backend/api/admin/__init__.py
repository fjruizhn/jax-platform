from .dashboard import router as dashboard_router
from .keys import router as keys_router
from .credentials import router as credentials_router
from .users import router as users_router
from .repository import router as repository_router
from .config_admin import router as config_router
from .usage import router as usage_router
from .memoria import router as memoria_router
from .models import router as models_router
from .facet_bindings import router as facet_bindings_router
from .motors import router as admin_motors_router
from .smtp import router as smtp_router
from .kill_switch import router as kill_switch_router
from .pipelines_ocultos import router as pipelines_ocultos_router

# facet_models_router (legacy, tabla `facet_models`) DESREGISTRADO el
# 2026-08-10: desde Bloque C nadie invoca con facet_models (la fuente es
# facet_binding/resolve_facet()) y su panel (AdminApiKeys.jsx) se retiró.
# El módulo se borró el 2026-09-16 (frente A, A-34). La tabla `facet_models`
# y su semilla quedan como dato histórico.

__all__ = [
    "dashboard_router",
    "keys_router",
    "credentials_router",
    "users_router",
    "repository_router",
    "config_router",
    "usage_router",
    "memoria_router",
    "models_router",
    "facet_bindings_router",
    "admin_motors_router",
    "smtp_router",
    "kill_switch_router",
    "pipelines_ocultos_router",
]
