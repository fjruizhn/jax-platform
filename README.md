# Axioma Platform (jax-platform)

La interfaz web del ecosistema JAX: un backend FastAPI que expone los
pipelines, el chat y la administración del sistema, y un frontend React
que le da cara de producto -- la Mesa de chat con las facetas, el panel
de administración de modelos y motores, y la vista en vivo de lo que
Jacobs está ejecutando.

En memoria de Jairo Urbina. En honor al Prof. Raúl Jacobs.

Este repo es la cara web del repo hermano `jax` (el motor de orquestación
en sí). Los dos comparten la misma base de datos y las mismas credenciales
de proveedor.

## Qué hace

- **Mesa de chat** -- conversación en tiempo real (WebSocket) con las
  facetas de JAX, con reconexión automática y estado por usuario.
- **Panel de administración** -- aprobación humana explícita para cambiar
  el modelo activo de una faceta (nunca un cambio automático o directo a
  base de datos), gestión de credenciales de proveedor, y visibilidad de
  qué motores tienen qué capabilities.
- **Constructor de pipelines** -- arma steps para Jacobs desde la UI,
  consultando en vivo qué combinaciones de motor/capability son
  ejecutables antes de dejar que el usuario arme un plan que Jacobs vaya
  a rechazar.
- **Autenticación** -- JWT (access de corta duración + refresh HttpOnly).

## Arquitectura, a alto nivel

```
backend/
  main.py       arranque de la app + estado compartido (EventBus, ResourceManager)
  api/          endpoints REST (chat, admin, pipelines)
  auth/         JWT + middleware
  db/           conexión a MariaDB + migraciones

frontend/
  src/
    i18n/       TODOS los strings de UI (es/en) -- ninguno hardcodeado en componentes
    store/      estado global (Zustand) + WebSocket
    components/ UI
    pages/      login + dashboard
```

El backend habla con el Motor Registry del repo `jax` (LAS MANOS) para
todo lo que requiera gobernanza de capabilities -- este repo no reimplementa
esa lógica, la consume.

## Instalación

Requisitos verificados contra el entorno real de desarrollo:

- Python 3.12+
- Node.js 20+ (probado con Node 24 vía nvm)
- MariaDB 11+ (misma base que el repo `jax`)
- El repo `jax` corriendo (o al menos su Motor Registry) para que el
  backend tenga con quién hablar

```bash
# backend
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# crear tu propio archivo de entorno con credenciales de DB/proveedor --
# mismas variables que espera el repo jax
uvicorn main:app --reload

# frontend (en otra terminal)
cd frontend
npm install
npm run dev
```

## Estado honesto

Funciona en producción hoy: chat en tiempo real, panel de administración
con el flujo de aprobación de modelos, y un constructor de pipelines que
valida contra el catálogo real de capabilities antes de dejar planificar
algo inejecutable.

Deuda conocida y documentada: hay tablas de base de datos con escritor
pero sin lector todavía (funcionalidad diseñada, no terminada de conectar
a una vista), y algunas rutas de despacho de facetas no pasan por la misma
capa de gobernanza que las demás -- es una limitación conocida del repo
hermano `jax`, no de este repo, pero afecta lo que este backend puede
garantizar sobre esas facetas específicas.

## Licencia

AGPL-3.0. Ver [LICENSE](LICENSE).

## Contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md).
