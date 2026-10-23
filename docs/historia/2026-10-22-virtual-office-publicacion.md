# Virtual Office: primera publicación pública

Fecha: 2026-10-22, hora local de Honduras. Tipo: HISTORIA. Responsable:
Codex, con GO de Fernando Ruiz para integrar y publicar. Los datos de salud y
rendimiento describen la comprobación de esta fecha; se vuelven a medir tras
cambios de código, esquema o infraestructura.

## Resultado y procedencia

La SPA está disponible en `https://virtualoffice.axioma-ia.io/virtual-office`.
La entrada pública sirve el frontend desde el release inmutable
`/opt/jax-office/virtualoffice.axioma-ia.io/releases/0515e64978177ef8945f8ed56928447d2a6d2f68`
en atem-ai. El servicio JAX de hall9000 usa el par JXP/JAX
`0515e64978177ef8945f8ed56928447d2a6d2f68-d62721f4438cd80a579e2ba48fcf05c04dffdc19`.
El manifiesto público tiene SHA-256
`1e4f4b011cd0771c6542c9f97ccb0eacb43dcd61a7a08bd57d5bf11622c9fd5d`;
el manifiesto pareado,
`7bc00586cfc4b7d82d0773bb261916306d01d76b824f74fbc9c7d65e7cb474df`.
Se cotejaron los SHA-256 de `index.html` y el asset JS entre el release y
HTTPS público: `d42069f4e33ae819ed62e39aca80b478c6d995c0c51d4ec2f8a69a200fb71819`
y `bede775bce5c187cdbfb3db18e1b386dba7da9b9228e0c3f63a74fb6b6ed799f`.

El smoke de ingreso público comprobó que la SPA responde 200, las rutas
Office sin credenciales responden 401, las rutas administrativas generales y
`/ws` quedan fuera del vhost (404), y `GET /api/apariencia` responde 200.
El timer y worker de invitaciones están activos y terminaron su ejecución con
resultado `success`. Se verificó previamente la conexión SMTP con autenticación
y TLS. Aún falta una invitación real de extremo a extremo después de que
Fernando active su cuenta; no se usó ni se pidió su contraseña por chat.

## Pruebas y rendimiento

- Suite integrada del par con MariaDB descartable: 80 pruebas pasadas.
- Frontend: 1518 pruebas pasadas y build de producción terminado.
- Carga pública: 200 solicitudes simultáneas al frontend, 200 respuestas 200,
  708 solicitudes/s, p95 84,7 ms; 200 solicitudes simultáneas sin credenciales
  a la sesión Office, 200 respuestas 401, 718 solicitudes/s, p95 92,8 ms.
  La prueba mide acceso anónimo y denegación; el flujo autenticado de usuario
  real queda pendiente de activar la cuenta.

## Restauración y pendientes

El release público anterior permanece en
`/opt/jax-office/virtualoffice.axioma-ia.io/releases/78560c8dcd90861bf095ddf7f04041ce6240c711`.
La instantánea del activador está en
`/var/lib/jax-office/activation-backups/office-activation.UrQuNy8v`.
El par backend anterior permanece en
`/opt/jax-platform-pair/releases/ac806dd013af6cf9addfbb62127e0d33767759fb-be924767eb7a08bfecef3fffff5cc1bf2e80ce4e`.
El procedimiento de reversión está en los runbooks de ingreso público y par.

La activación humana del usuario raíz es el siguiente paso de producto:
Fernando entra con su cuenta existente y contraseña actual en el sitio. Luego
se comprueba una invitación por correo real y la administración delegada de
usuarios/proyectos. Los PR de fuente #238 (base JXP) y #410 (JAX) ya se
integraron; #229 (JXP) espera que termine la corrección y comprobación de su
suite con MariaDB. Los commits de piso CI posteriores a los SHA de runtime no
cambian este release. Continúan por separado el manifiesto #351 y el selector
#352.

Alternativa descartada: usar el checkout de Git de usuario como árbol ejecutado
en producción. El release seleccionado usa árboles inmutables y manifiestos
verificados, con un par JXP/JAX explícito. La publicación y la decisión de
permitir modelos externos, administradores de proyecto e invitaciones por
correo fueron de Fernando; los detalles técnicos de release y pruebas fueron
de Codex, con revisión adversarial independiente del SHA de runtime.
