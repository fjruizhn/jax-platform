# Paperclip · endurecimiento de JAX Platform · 2026-11-24

## Hecho

El frontend acepta enlaces externos solo si son URLs absolutas `http` o `https`; los valores con otros esquemas quedan como texto o no muestran el enlace de PR. El runner del Ejecutor recibe una lista explícita de variables usadas por `jax/ejecutor`, en lugar de heredar todo el entorno del servicio. La configuración del frontend estático genera una CSP cuyo hash sale del script `tema-inicial` del `dist/index.html` que se entrega.

## Por qué

Las fuentes de pipelines y el URL de un PR son datos externos. El proceso de ejecución no necesita secretos de sesión, correo, Telegram ni OAuth de Claude. El script de tema debe correr antes de React sin abrir `unsafe-inline` en CSP.

## Límites

La CSP queda configurada para el modo de frontend estático definido en `ops/frontend/switch.py`. Instalarla en aaPanel/nginx de producción requiere la autoridad operativa correspondiente. Sacar `FERNET_KEY` o dependencias de JAX del runner requerirá cambiar el contrato de capacidades, por lo que no forma parte de este cambio.
