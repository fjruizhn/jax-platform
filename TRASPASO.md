# OP#270 / P100 — frontend compilado en Hall9000

Codex · 2026-10-14. Encargo: /home/fruiz/ENCARGO-p100-jax-frontend-build-20261014.md; OP#270. Decisión de servidor estático ya tomada por Fernando mediante Hyde; despliegue reservado a Hyde después de auditoría de escalón 3.

## Hecho

- Rama propia desde origin/master 5eac74efc08f4e8fa1b880ee5eb0fb566d2eb2f2, igual al SHA observado en producción; el checkout viejo fix/tenant-user-mutex permanece intacto.
- Marca publicada en claude-skills/PENDIENTES.md, commit e1e551d6; OP#270 en curso, asignado a Codex con su usuario6.
- No hay CONTEXT.md/CLAUDE.md/AGENTS.md en raíz de jax-platform ni en origin/master. Se leyeron la documentación de migración y el runbook de despliegue.
- npm ci limpio: Node24.16.0, npm11.13.0, Vite6.4.3, React19.2.7; baseline frontend1414passed/0failed.
- Unidad viva frontend PID8108, Userjaxsvc, checkout /srv/jax-prod, dos drop-ins efectivos; nginx global inactivo. Inventario sólo lectura. g1 no intervenida.
- Explorador y arquitecto son agentes distintos de sólo lectura; diseño técnico de Sol recibido; contrato en docs/superpowers/plans/2026-10-14-p100-frontend-build.md.

## Falta

Implementar build por SHA y nginx con proxies de Vite; reversión en un comando con pruebas; comparar carga mediante el mismo arnés y probar login/datos en claro/oscuro; publicar PR y auditoría independiente. La carga y reversión previas a auditoría se ensayan en puertos y unidades privados. La medición final de producción y el despliegue real quedan a Hyde (OP#270, comentario734).

## Restricciones y decisiones

No escribir en /srv/jax-prod, /etc ni sitio de atem-ai; no parar/reiniciar servicios reales; no usar cuentas reales ni tocar g1. /api y /ws conservan ruta/query y Host del upstream con changeOrigin; assets faltantes no caen al SPA. No se presenta un ensayo aislado como cambio productivo.

El cliente DEV usa WS:8080 con hostname localhost; el navegador del ensayo usará hostname permitido mapeado a loopback sólo en su proceso y el upstream Vite se redirigirá al fixture. No abrirá el backend vivo por accidente.

## Siguiente comando

Desde esta rama: git status --short
Ejecutar Task1 con implementador Terra y revisión Sol separada; después Task2 y Task3. Build base pasó; advertencia preexistente de chunk JS mayor de500kB permanece sin rebajar umbrales. Evidencia cruda: /home/fruiz/veredictos-ensayo/p100-jax-frontend-20261014/.
