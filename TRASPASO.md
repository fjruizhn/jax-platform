# Traspaso — jax-platform C5 solo órdenes — 2026-10-06

Estado: PR #209 abierto, rama `feat/c5-solo-ordenes`, SHA `373809aebcd709229b02262b8ced9d4867bb37e8`. No integrar.

- Worktree propio: `/home/fruiz/wt/jxp-c5-solo-ordenes`; checkout principal de jax-platform sin tocar.
- Seed nueva `ejecutor.c5_auditor_nube_solo_ordenes=false`, administrada de forma estricta por `/admin/config`, booleano solo `true`/`false`, superadmin y auditoría existentes.
- Hosts con datos de clientes solo resultan elegibles por la opción si el binding configurado del auditor es de proveedor cloud real y el proveedor se distingue del cerebro, salvo la compuerta explícita existente.
- AdminSettings checkbox sin valor abierto por omisión; cadenas es/en y error de validación traducido.
- Auditoría escalón 3 del SHA exacto: APROBADO, 0 BLOCK / 0 MAJOR / 0 MINOR.
- Verificación: frontend completo 1391 passed; AdminSettings 34 passed; build correcto; backend focal C5 30 passed / 89 skipped; backend no-DB completo 2366 passed / 1474 skipped, sin DB productiva.
- Pisos CI medidos: frontend 1391; backend con DB 3838 (base 3830 + 8 pruebas puras); backend sin DB 2366.
- Suite backend completa con DB no ejecutada localmente: su guardia detectó puerto productivo 3308. No reintentar con esa configuración.
- PR: https://github.com/fjruizhn/jax-platform/pull/209. Checks iniciales pendientes al momento de abrir; comprobarlos contra el head SHA antes de dar CI por verde.
- No merge ni despliegue.
