# Virtual Office · evidencia de entrada web

2026-10-20 · Codex · OP#343. HECHO con procedencia, no autorización ni despliegue.

Raíz verificó en vivo `systemctl is-active` para jax-platform/jax-las-manos/jax-platform-frontend y piloto de usuario: cuatro activos. `ss -ltn` confirma piloto127.0.0.1:4173, LASMANOS127.0.0.1:7777, plataforma0.0.0.0:8080, frontend0.0.0.0:5173. DNS público consultado a1.1.1.1 devuelve NXDOMAIN para virtualoffice.axioma-ia.io; axioma-ia.io A38.7.24.147. Esto caduca y se repite antes de modificar.

Fuente adicional: explorer office_ingress_discovery informó nginx aaPanel en VM172.16.20.11 sirve axioma-ia.io y proxy /api,/ws a Hall9000:8080, certificado en vhost propio, sin virtualoffice. Su inspección de configuración remota aún no fue repetida por raíz: tratar detalle como HECHO no verificado independientemente. Referencias: ops/migration/MIGRATION.md y ops/migration/nginx/jax-platform-frontend.conf del snapshot desplegado. Merge no mostró workflow deploy en inventario del agente; verificar árbol vigente y CI antes de integrar.

DECISIÓN de implementación vigente: reutilizar backend JAX Platform y autoridad canónica, datos de oficina aislados por namespace/grants. Seleccionar modo de servir frontend/API en contrato de despliegue; un proceso separado del mismo backend exige revisar todos los workers/schedulers para evitar duplicación, pools y sesión. No asumir que una recomendación de discovery reemplaza arquitectura aprobada. No exponer piloto4173 ni copiar su montaje estático abierto.

PENDIENTE del plan Task6: verificar vhost remoto y emisión TLS, permisos/credenciales de despliegue, DNSCloudflare, hosts permitidos, origen/cookies/CSRF, artefactos/servicios/puerto y rollback; no modificar correo ni otros vhosts. Backup/restauración y carga preceden lanzamiento.
