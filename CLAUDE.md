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

## Lecciones operativas (sesión 2026-06-22)

1. DEPLOY FRONTEND: requiere rsync EXPLÍCITO de hall9000 a la VM dev.
   `npm run build` solo genera el bundle en /home/fruiz/jax-platform/frontend/dist/
   en hall9000 — NO actualiza producción. El público sirve estático desde
   /www/wwwroot/axioma-ia.io/ en la VM dev (172.16.20.11).
   Procedimiento: rsync dist a /tmp/axioma-deploy/ en la VM, luego
   `sudo rsync -a --delete --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/`.

2. ARQUITECTURA REAL: nginx/aaPanel vive en la VM dev (172.16.20.11), NO en
   hall9000. El vhost axioma-ia.io sirve estático desde wwwroot y proxya /api
   y /ws a http://172.16.20.5:8080 (hall9000) con upgrade headers para WS.

3. ROTACIÓN JWT: rotar JAX_JWT_SECRET invalida TODAS las sesiones activas.
   Requiere re-login + limpieza de localStorage/cookies en cada navegador.
   Síntoma de token huérfano: 401 en /api/state con "Authorization: Bearer"
   presente en el request.

4. MOTOR jax_local: ollama local en GPU AMD Radeon AI PRO R9700 32GB ROCm
   (antes RX 9060 XT 16GB Vulkan, migrada jul-2026). El modelo activo lo
   define la tabla `facet_models` (is_active), NO `config.toml` — ver
   [[facet-models-stale-active-flag-jax-local]] en memoria: si se cambia el
   modelo a mano en Ollama sin actualizar esa fila, la próxima request lo
   revierte. Actualmente qwen3-coder:30b (~21GB). El keep_alive:-1 está
   embebido en _call_ollama (backend/api/chat.py) para evitar recarga en frío.
   Si reaparece lentitud de ~60s, verificar con `ollama ps` que UNTIL no
   expire pronto y que el modelo cargado coincida con la fila activa de DB.

5. DIAGNÓSTICO POR EVIDENCIA: verificar vhost/hashes/procesos antes de ejecutar
   planes. En esta sesión, 2 de 3 causas raíz diagnosticadas por suposición
   resultaron falsas. "El que supone se equivoca."
