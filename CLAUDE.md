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
7. El público (axioma-ia.io) sirve el Vite DEV server de hall9000 vía proxy
   (ver Lecciones operativas #1/#2) — un cambio en frontend/src ya es visible
   en vivo sin build ni deploy. `npm run build` + rsync a wwwroot NO tiene
   efecto hoy (nadie sirve ese estático); no lo tomes como paso obligatorio.

## SERVICIOS RELACIONADOS
- LAS MANOS: http://127.0.0.1:7777
- Jacobs: http://127.0.0.1:7777/jacobs/
- Motor Registry: http://127.0.0.1:7777/motor/

1. DEPLOY FRONTEND — CORREGIDO 2026-08-02 (ver #1-bis abajo): esta entrada
   original decía que había que rsyncear un build estático a la VM dev y que
   eso era lo que servía el público. Verificado con evidencia (curl + cat del
   vhost real) que es FALSO desde al menos 2026-08-02: el vhost nginx proxya
   TODO `location /` al Vite dev server de hall9000:5173, así que el rsync
   a wwwroot queda muerto (nadie lo sirve). Se deja el procedimiento por si
   se revierte a static hosting en el futuro:
   `npm run build` genera /home/fruiz/jax-platform/frontend/dist/ en hall9000;
   rsync a /tmp/axioma-deploy/ en la VM, luego
   `sudo rsync -a --delete --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/`.
   Antes de asumir que este procedimiento aplica, releer #1-bis y confirmar
   con `sudo cat /www/server/panel/vhost/nginx/axioma-ia.io.conf` en la VM.

1-bis. ARQUITECTURA REAL (verificada 2026-08-02): nginx/aaPanel vive en la VM
   dev (172.16.20.11:58291 SSH, user fruiz). El vhost axioma-ia.io tiene
   `location /` con `proxy_pass http://172.16.20.5:5173` — proxya TODO al
   Vite DEV server que corre en hall9000 como systemd `jax-platform-frontend`
   (`vite dev --host 0.0.0.0 --port 5173`). `location /api` y `location /ws`
   sí proxyan a http://172.16.20.5:8080 (hall9000) como documentado.
   Consecuencia: `/www/wwwroot/axioma-ia.io/` (el estático) existe en disco
   pero NO está en la ruta de servicio — un cambio en frontend/src ya se ve
   en vivo vía HMR del dev server, sin build ni deploy. No confundir "dev"
   en el nombre del dominio con un ambiente real de dev separado del código
   fuente: es literalmente el dev server expuesto al público.

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
