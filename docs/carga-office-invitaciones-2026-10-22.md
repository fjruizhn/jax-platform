# Carga y restauración aisladas · invitaciones de Virtual Office

> **HISTORIA RECHAZADA PARA CAPACIDAD (2026-10-22).** Las métricas que siguen
> son la corrida original preservada, no un resultado apto para GO: el rótulo
> de concurrencia representaba `c × 4` solicitudes simultáneas, la lista no
> contenía el peor caso de 100 invitaciones y el limitador se heredaba entre
> niveles. El JSON histórico permanece en
> `docs/evidence/office-invitations-load-2026-10-22.json` con SHA-256
> `0bd855de77656cf8894dee8c390302b593d74beaf3c9e71d1e6af996521980bf`.
> La medición corregida y separada está en
> `docs/carga-office-invitations-2026-10-22.md` y
> `docs/evidence/office-invitations-load-corrected-2026-10-22.json`.

2026-10-22 · Codex · HISTORIA verificada de una corrida rechazada. Rama de medición `codex/office-invitation-load-20261022`, basada en PR #218 `b871dc6ee215099686f6ccf3ff732cb31fdafa10`. El arnés levantó el ASGI real en loopback, entró por login y MFA reales, y usó MariaDB 12.3.3 desechable `cv-op343-db-20261020`, esquema nuevo `jax_memory_test_1a2b3c4d`. Los detalles reproducibles están en `loadtest/office_invitations.py` y los datos íntegros en `docs/evidence/office-invitations-load-2026-10-22.json` (SHA-256 `0bd855de77656cf8894dee8c390302b593d74beaf3c9e71d1e6af996521980bf`). No se llamó al dominio público ni a la base productiva.

| Operación | c=1 p95/RPS | c=5 p95/RPS | c=10 p95/RPS | Resultado |
|---|---:|---:|---:|---|
| Listar activas | 34.55 ms / 15.70 | 27.91 ms / 46.67 | 60.43 ms / 83.30 | 0 errores |
| Emitir para aceptar | 12.47 ms / 300.61 | 57.92 ms / 313.10 | 124.77 ms / 292.50 | 0 errores |
| Aceptar | 176.51 ms / 20.03 | 207.79 ms / 61.04 | sin p95 exitoso / 148.89 | c=1: 4/4 HTTP 200; c=5: 13/20 HTTP 200 y 7/20 HTTP 429; c=10: 0/40 HTTP 200 y 40/40 HTTP 429 |
| Emitir para revocar | 10.50 ms / 357.32 | 58.12 ms / 323.91 | 107.15 ms / 340.56 | 0 errores |
| Revocar | 10.35 ms / 344.89 | 46.60 ms / 373.82 | 89.04 ms / 391.82 | 0 errores |

El primer límite observado para la aceptación fue el limitador real de solicitudes por IP: todos los clientes de este ensayo comparten `127.0.0.1`. Esta observación histórica no se interpreta como capacidad de aceptación ni sostiene publicación. La política para usuarios legítimos detrás de una misma IP sigue pendiente de revisión de seguridad.

Restauración de ensayo: un `mariadb-dump --single-transaction --routines --triggers --events` del esquema de carga produjo `/tmp/office-invitations-restore-20261022.sql` de 793507 bytes, SHA-256 `71a1cb012a4ea92ca81f11bf976ef9f4a1cccd5ca184460ea222191f71c07faf`. Para restaurarlo en `jax_memory_test_5e57a11d` se reescribieron solo las referencias calificadas al nombre del esquema en DDL/comentarios; ninguna línea `INSERT INTO` contenía ese nombre. El archivo restaurado tuvo SHA-256 `d4d262bfc11d52e9db258a90bf6ca82099afece7cf0dbaf9141eabab1a69b214`. La importación terminó con exit 0. Origen y destino coincidieron en 102 tablas, 1 vista, 33 triggers y conteos de `office_invitation` 192, `office_invitation_outbox` 192, `office_invitation_event` 415, `office_project_grant` 93, `office_account_profile` 78 y `jax_project_scope` 73. `CHECKSUM TABLE ... EXTENDED` coincidió en las seis tablas; la vista `motor_resolved` y una consulta de invitaciones con JOIN funcionaron en el destino. Los dos esquemas se conservan para inspección.

Esta restauración prueba el artefacto y la ruta del ensayo desechable. Falta verificar restauración del respaldo real de producción y ejecutar carga por el dominio/TLS final cuando exista. #218 sigue draft y el sitio público no está activado.
