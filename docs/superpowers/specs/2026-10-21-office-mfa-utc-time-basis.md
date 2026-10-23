# MFA time basis: database UTC

2026-10-21 · DECISIÓN técnica de Codex, con la observación de producción
verificada por la sesión padre. La frontera MFA usa `UTC_TIMESTAMP(6)` para
toda lectura y escritura de sus instantes `DATETIME(6)`. No cambia la zona
del pool ni el comportamiento Classic.

## Evidencia

Una transacción de solo lectura contra MariaDB de producción en `127.0.0.1:3308`
midió `@@session.time_zone='SYSTEM'`, `@@system_time_zone='CST'`; en la misma
lectura `NOW(6)` estaba seis horas detrás de `UTC_TIMESTAMP(6)`. Por tanto,
`NOW(6)` no era una base válida para TOTP, expiraciones ni recencia MFA.

La misma comprobación confirmó que todavía no existen tablas `office_mfa_*` y
que `jax_users` aún no tiene `mfa_version`: no hay filas MFA desplegadas cuya
marca de tiempo deba migrarse. Es evidencia de ausencia de dominio desplegado,
no un conteo de filas.

## Contrato

- `_db_now` obtiene `UTC_TIMESTAMP(6)`; TOTP, expiración, presupuestos y
  recencia comparan esa base de datos UTC.
- Cada escritura MFA de `DATETIME(6)` expresa `UTC_TIMESTAMP(6)`, incluidos
  `occurred_at` de prueba/cambio de estado y el `SYSTEM_FENCE` de migración.
- Los recibos convierten valores `DATETIME` a UTC consciente: un valor sin zona
  se interpreta como UTC; uno consciente se convierte a UTC.
- `last_login`, si lo introduce el finalizador de login de 3B, conserva su
  contrato propio `NOW()` y no forma parte de esta tarea.

Los ensayos reales fijan la sesión a `-06:00` y `+05:30`, restauran la zona en
`finally`, y verifican que un OTP generado ahora es aceptado y que las marcas
MFA permanecen cercanas a la hora UTC de la base. Cambiar la zona global del
pool habría modificado semántica Classic fuera de este contrato.
