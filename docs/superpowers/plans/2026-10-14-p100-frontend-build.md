# P100 / OP#270 — plan técnico de frontend compilado

Codex, 2026-10-14. Especificación vinculante: encargo de Fernando mediante Hyde, `/home/fruiz/ENCARGO-p100-jax-frontend-build-20261014.md`; OP#270, comentarios734/768. Concepto autorizado: nginx estático con build reproducible, reversión probada y comparación de carga. Diseño técnico: Sol `/root/p100_diseno`, sólo lectura; no equivale a auditoría del código.

## Global Constraints

- No desplegar, escribir ni reiniciar en `/srv/jax-prod`, `/etc` ni atem-ai; no tocar g1, cuentas reales ni backend vivo. Sólo procesos, puertos, unidades y archivos privados del ensayo.
- Fuente: rama propia desde master5eac74ef, idéntico al SHA de producción observado. No cambiar frontend/src, traducciones, tema, autenticación ni DB. No inyectar APP_VERSION ni variables de build no declaradas.
- Mantener `/api` y `/ws` tal como los define Vite: match por prefijo, path/query conservados, Host de target con changeOrigin, Origin sin reescribir y WebSocket con Upgrade. No inventar rutas API ni cabeceras de confianza nuevas.
- Assets faltantes y rutas privadas devuelven404, nunca index.html. SPA y /index.html: no-cache. Sólo assets hashados: cache larga immutable; assets de releases anteriores conservados. Sin cambios de UI: comprobar claro/oscuro e i18n.
- Build limpio por SHA40, npm ci con lockfile, Node/npm fijados desde lo instalado y entorno explícito. Dos builds de la misma entrada deben dar los mismos paths/bytes; no publicar si falla. Manifiesto sin tiempo variable; fecha del sistema sólo en recibos.
- Nginx independiente bajo la misma unidad frontend, sin activar nginx global; drop-in gestionado único, conserva la base y todos los drop-ins anteriores. Aplicación y rollback bajo el mismo flock, detectan cambios vivos y usan journal durable recuperable antes del preflight ordinario.
- No confundir escritura DAC con barrera contra sudo. Artefactos/config/recibos futuros de producción root-owned sin go+w; no se modifica política sudo en este PR.
- El ensayo aislado no sustituye medición final en producción. Hyde mide antes/después y ordena despliegue sólo después de auditoría. No presentar mock como autenticación o backend productivo.
- Todo cambio por PR con OP#270 y LISTO PARA AUDITORÍA SHA. No integrar. Commit Codex con footer; sólo root hace commit/push/gh y registro OpenProject.

### Task 1: Build, nginx y transacción de reversión

Responsabilidad: ops/frontend/ y config nginx de referencia; pruebas de contrato bajo ops/frontend/tests/. CLI de build devuelve release y manifiesto por SHA; controlador de release ofrece preparación/render, apply y rollback con rutas/config explícitas y modo privado de ensayo sin producción. Defaults en fichero de configuración, no datos variables embebidos en lógica.

Archivar fuente Git exacta fuera del checkout, rechazar .env y ambiente VITE_ implícito, fijar Node24.16.0/npm11.13.0, npm ci y dos builds comparados. Releases/<SHA>/dist con inventario y lockfile/runtime/proxy contract; pool union de assets por hash, colisión de bytes falla cerrado. No borrar releases/GC. Renderer de nginx completo; paridad del proxy deriva de config Vite real, no lista inventada; nginx1.28.3 con cabeceras de cache/release repetidas donde add_header corta herencia.

Reversión en un comando, pre/post fingerprints de unidad efectiva y todos sus drop-ins; sólo cambia su drop-in/config/current gestionados. Prepara/valida nginx y unidad antes de parar el servicio. Journal con fases y fsync; reentrada tras corte recupera Vite conocido antes de trabajo nuevo, y se niega sin señales ante cambios desconocidos. Un error de reinicio o comportamiento vuelve a Vite; si restaurar falla se registra error y no declara éxito. Ensayos de daños, carrera y fallos con estado observable, sin assertions sobre texto fuente.

### Task 2: Ensayo de navegación, carga y reversión real

Responsabilidad: loadtest/frontend_* y sus pruebas, fixture API/WS y navegador. Usa interfaz/render de Task1, no reimplementa controlador.

Mismo SHA y arnés para Vite y nginx en puertos privados. Fixture local con apariencia, refresh401 antes de login, login/me/state/proyectos y WS auth_ok; login real de UI, datos marcador, claro/oscuro y español/inglés. Host axioma-ia.io resuelto a loopback sólo dentro de Chromium evita el atajo DEV WS a8080; ningún request va a backend real. Probar cuerpos grandes, path/query/Host/Origin en API/WS, API404, assets404, cache, host inválido y release anterior.

Carga con guion único: HTML+ruta UI+recursos descubiertos; warm-up y niveles1/8/32/64/128, tres repeticiones comparables, mediana RPS/p95/error; criterio de degradación explícito antes del ensayo. Extender niveles sólo con evidencia si no se alcanza degradación, registrar límite censurado si no se midió. Rollback real de unidad privada Vite→nginx→Vite con drop-ins canario; cortes y recuperación en fronteras, sin matar procesos ajenos.

### Task 3: Integración, Biblioteca y auditoría

Responsabilidad root: CI necesaria para nuevas pruebas, docs/runbooks/despliegue.md y runbook P100 específico, informe de resultados con fecha/fuentes/alcance/riesgos, TRASPASO y PR. Mantener pisos actuales (frontend1414) si no cambia colección frontend; nuevas pruebas operativas tienen job propio. Validar frontend y contratos nuevos, verificar diferencias sobre master y después emitir SHA exacto para Sol adversarial independiente. Archivar TRASPASO y retirarlo antes de la auditoría final. Registro en OP#270 firmado Codex; entrega al despliegue de Hyde, no producción.

## Contratos entre tareas

Task1 produce artefacto/manifest/config/render y CLI transaccional; Task2 consume esos contratos en servidores reales privados. Ambos producen pruebas que Task3 cablea en CI. Root es único dueño de docs/CI/commits. Ningún agente revierte cambios de otro ni genera revisores secundarios.
