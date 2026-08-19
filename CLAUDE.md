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
7. Rebuild + deploy frontend después de cada cambio (restaurado 2026-08-02,
   ver Lecciones operativas #1): cd frontend && npm run build, luego rsync
   del build a la VM dev. Un cambio en frontend/src NO se ve en vivo hasta
   hacer esto — el público sirve estático, no el dev server.

## SERVICIOS RELACIONADOS
- LAS MANOS: http://127.0.0.1:7777
- Jacobs: http://127.0.0.1:7777/jacobs/
- Motor Registry: http://127.0.0.1:7777/motor/

## Lecciones operativas (sesión 2026-06-22, corregida 2026-08-02)

1. DEPLOY FRONTEND: requiere rsync EXPLÍCITO de hall9000 a la VM dev.
   `npm run build` solo genera el bundle en /home/fruiz/jax-platform/frontend/dist/
   en hall9000 — NO actualiza producción. El público sirve estático desde
   /www/wwwroot/axioma-ia.io/ en la VM dev (172.16.20.11:58291 SSH, user fruiz).
   Procedimiento: rsync dist a /tmp/axioma-deploy/ en la VM, luego
   `sudo rsync -a --delete --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/`.

1-bis. ARQUITECTURA REAL (verificada y restaurada 2026-08-02): nginx/aaPanel
   vive en la VM dev. El vhost axioma-ia.io (`/www/server/panel/vhost/nginx/axioma-ia.io.conf`)
   tiene `root /www/wwwroot/axioma-ia.io` + `location / { try_files $uri $uri/ /index.html; }`
   (fallback SPA para react-router) y `location /assets/` con cache 1y
   immutable. `location /api` y `location /ws` proxyan a
   http://172.16.20.5:8080 (hall9000) con upgrade headers para WS.

   HISTORIAL: entre una fecha desconocida y 2026-08-02, `location /` estuvo
   apuntando por error a `proxy_pass http://172.16.20.5:5173` (el Vite DEV
   server de hall9000, systemd `jax-platform-frontend`), dejando el estático
   de wwwroot sin servir — el público veía el dev server con HMR en vivo.
   Detectado por evidencia (curl mostraba scripts @vite/client) y corregido
   el mismo día. Backup del vhost roto: `axioma-ia.io.conf.backup-20260802061023`.
   Si algo vuelve a andar raro en prod, confirmar con
   `sudo cat /www/server/panel/vhost/nginx/axioma-ia.io.conf` en la VM antes
   de asumir que el deploy estático sigue vigente — "el que supone se equivoca".

3. ROTACIÓN JWT: rotar JAX_JWT_SECRET invalida TODAS las sesiones activas.
   Requiere re-login + limpieza de localStorage/cookies en cada navegador.
   Síntoma de token huérfano: 401 en /api/state con "Authorization: Bearer"
   presente en el request.

4. MOTOR jax_local: ollama local en GPU AMD Radeon AI PRO R9700 32GB ROCm
   (antes RX 9060 XT 16GB Vulkan, migrada jul-2026). CORREGIDO 2026-08-19 —
   esta nota decía que el modelo activo lo define `facet_models` (is_active);
   eso quedó obsoleto con el refactor Bloque C del 2026-08-09
   (facet_resolver.py). El modelo real lo define `facet_binding` (role=
   'primary', columna model_ref), resuelto en vivo por `resolve_facet()` —
   ver facet_resolver.py, usado tanto por backend/api/chat.py como por
   jacobs/executor.py y jacobs/plan.py. `facet_models` es una tabla catálogo
   distinta (opciones disponibles para UI), no la fuente de verdad — no
   confundirlas.
   Cambiar el modelo NUNCA es un UPDATE a mano: el único endpoint que escribe
   facet_binding es `POST /admin/models/proposals/{id}/approve` (backend/api/
   admin/models.py, requiere superadmin) — "regla de oro" explícita en el
   código: nunca el sync, siempre una aprobación humana. Flujo: `ollama pull`
   el modelo nuevo → `POST /admin/models/sync` lo registra en la tabla
   `model` → se crea un `model_binding_proposal` (pending) → se aprueba desde
   el panel admin de Axioma Platform.
   Upgrade 2026-08-19: qwen3.6:35b-a3b-q4_K_M pulleado, proposal (id 2,
   reason='new_model_available') aprobada 03:45:42 — `facet_binding.model_ref`
   de jax_local confirmado apuntando a qwen3.6:35b-a3b-q4_K_M y `ollama ps`
   lo muestra cargado en GPU ("UNTIL Forever"). Una proposal posterior (id 3,
   reason='drift_detected') fue rechazada — no es un cambio real. Verificado
   con evidencia, no supuesto — "el que supone se equivoca".
   Los modelos de Ollama ahora viven en
   `/srv/jax-data/ollama-models` (antes en `/`, que llegó a 86% de uso
   compartiendo disco con MariaDB — deuda de infraestructura resuelta ese
   mismo día, ver Environment=OLLAMA_MODELS en el drop-in
   `/etc/systemd/system/ollama.service.d/override.conf`).
   El keep_alive:-1 sigue embebido en _call_ollama (backend/api/chat.py) para
   evitar recarga en frío. Si reaparece lentitud de ~60s, verificar con
   `ollama ps` que UNTIL no expire pronto y que el modelo cargado coincida
   con `facet_binding.model_ref` para jax_local (no con `facet_models`).

5. DIAGNÓSTICO POR EVIDENCIA: verificar vhost/hashes/procesos antes de ejecutar
   planes. En esta sesión, 2 de 3 causas raíz diagnosticadas por suposición
   resultaron falsas. "El que supone se equivoca."

6. CONFIG.TOML CACHEADO (2026-08-08): `_load_config()` en backend/api/chat.py
   ahora usa `@lru_cache(maxsize=1)` — se lee una sola vez por proceso, no en
   cada request de chat. Un cambio manual en config.toml NO se refleja hasta
   `systemctl restart jax-platform`. Mismo comportamiento que
   jacobs/executor.py, que ya cargaba config.toml una sola vez al importar.
