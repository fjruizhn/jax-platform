# Traspaso: aviso Telegram del freno de incertidumbre

- **Objetivo:** completar el pendiente `[jax-platform] aviso Telegram cuando se activa el freno de incertidumbre de reprocesar/despacho`.
- **Hecho:** branch `fix/incertidumbre-telegram-codex-20261005` desde `origin/master`; el despachador encola un aviso por activación con `lib-avisar.sh`; systemd entrega `telegram.env` como credencial privada; pruebas TDD y documentación agregadas.
- **Verificado:** `backend/tests/test_proyectos_documentos_despachador.py`: 150 passed contra MariaDB desechable `127.0.0.1:33991`. Backend sin DB: 2342 passed, 1459 skipped, 3 warnings.
- **Decisiones:** seguir el contrato del encargo de Fernando; no tocar `PENDIENTES.md` ni producción; usar `LoadCredential` porque `jaxsvc` no puede leer `/etc/restic/telegram.env`; Hyde audita con escalón 3 e integra.
- **Falta:** commit y push de la rama, crear un PR único, enviar URL y SHA a la sesión `fruiz-b1` para auditoría.
- **Siguiente comando:** `git status --short --branch` en `/home/fruiz/wt/jxp-incertidumbre-telegram`.
