# Axioma Platform — Contexto para Claude Code

## LEER PRIMERO: ~/.claude/CLAUDE.md (políticas globales)

## ESTE PROYECTO
Axioma Platform es la interfaz visual de JAX — la cabina de mando del ecosistema.
NO es AteneaERP. NO mezclar. Son productos independientes.

## ESTRUCTURA
~/jax-platform/
  backend/          FastAPI :8080
    main.py         App principal + startup
    jax_engine/     Estado + EventBus + ResourceManager
    auth/           JWT + middleware
    api/            Endpoints REST
    db/             MariaDB connection + migrations
  frontend/         React 19 + Vite :5173
    src/
      i18n/         TODOS los strings aquí
        es.js       Español (idioma base)
        en.js       Inglés
        index.js    Configuración react-i18next
      store/
        useJaxStore.js    Estado global Zustand
        useWebSocket.js   WebSocket con reconexión
      components/   Componentes UI
      pages/        Login + Dashboard

## REGLAS ESPECÍFICAS DE ESTE PROYECTO
1. El ojo HAL es el centro — no decoración, es telemetría emocional
2. Los colores de facetas son sagrados (ver useJaxStore.js)
3. WS canal por usuario — nunca por tenant
4. Kill switch siempre visible en la UI
5. Panel derecho = "Director Jacobs" (no "Jacobs")
6. Jacobs NO aparece en el panel de facetas izquierdo
7. Rebuild frontend después de cada cambio: cd frontend && npm run build

## SERVICIOS RELACIONADOS
- LAS MANOS: http://127.0.0.1:7777
- Jacobs: http://127.0.0.1:7777/jacobs/
- Motor Registry: http://127.0.0.1:7777/motor/
