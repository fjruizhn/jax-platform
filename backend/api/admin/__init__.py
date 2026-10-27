"""Lazy classic JAX router exports; Office imports only the SMTP module."""
from importlib import import_module

_ROUTERS = {
    "dashboard_router": "dashboard", "keys_router": "keys",
    "credentials_router": "credentials", "users_router": "users",
    "repository_router": "repository", "config_router": "config_admin",
    "usage_router": "usage", "memoria_router": "memoria",
    "models_router": "models", "facet_bindings_router": "facet_bindings",
    "admin_motors_router": "motors", "smtp_router": "smtp",
    "kill_switch_router": "kill_switch", "pipelines_ocultos_router": "pipelines_ocultos",
}


def __getattr__(name):
    module = _ROUTERS.get(name)
    if module is None:
        raise AttributeError(name)
    return import_module(f".{module}", __name__).router

# facet_models_router (legacy, tabla `facet_models`) DESREGISTRADO el
# 2026-08-10: desde Bloque C nadie invoca con facet_models (la fuente es
# facet_binding/resolve_facet()) y su panel (AdminApiKeys.jsx) se retiró.
# El módulo se borró el 2026-09-16 (frente A, A-34). La tabla `facet_models`
# y su semilla quedan como dato histórico.

__all__ = list(_ROUTERS)
