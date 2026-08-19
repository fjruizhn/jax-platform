from .dashboard import router as dashboard_router
from .keys import router as keys_router
from .credentials import router as credentials_router
from .users import router as users_router
from .repository import router as repository_router
from .config_admin import router as config_router
from .usage import router as usage_router
from .models import router as models_router
from .facet_bindings import router as facet_bindings_router
from .motors import router as admin_motors_router

# facet_models_router (legacy, tabla `facet_models`) DESREGISTRADO
# (2026-08-10): dejo de tener ningun consumidor real desde que Bloque C
# corto la invocacion a facet_binding/resolve_facet() — chat.py.
# _resolve_active_model y jacobs/executor.py._get_active_model, las unicas
# funciones que leian facet_models para decidir que modelo invocar, estaban
# muertas (nunca llamadas) y se borraron el mismo dia. El panel admin
# (AdminApiKeys.jsx) era el UNICO consumidor de este router — mostraba y
# dejaba editar un "modelo activo" que ya no controlaba nada real. Dos
# pestañas de la misma app podian mostrar valores distintos para el mismo
# hecho (thot: facet_binding=gpt-5.5 vs facet_models=gpt-5.6-terra) porque
# una de las dos ya no era la fuente real. La tabla `facet_models` y este
# modulo se conservan (dato historico, sin borrar en esta corrida) pero el
# router ya NO se registra en main.py — cierra el ultimo camino de escritura
# a una tabla que puede volver a desincronizarse de facet_binding sin que
# nada la use ni lo note.
# from .facet_models import router as facet_models_router

__all__ = [
    "dashboard_router",
    "keys_router",
    "credentials_router",
    "users_router",
    "repository_router",
    "config_router",
    "usage_router",
    "models_router",
    "facet_bindings_router",
    "admin_motors_router",
]
