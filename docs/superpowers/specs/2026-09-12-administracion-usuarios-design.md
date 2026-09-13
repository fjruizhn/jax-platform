# Administración de usuarios — diseño

**Fecha:** 2026-09-12 · **Estado:** aprobado por Fernando (diseño en chat) · **Repo:** jax-platform

## 1. Por qué

Fernando pidió cambiar contraseñas, modificar y eliminar usuarios (solo superadmin), con
la confirmación por suma que exige la política. Al medir el código aparecieron huecos más
graves que lo pedido. Todo lo de esta sección está **verificado en el código o en la base
el 2026-09-12**, no supuesto.

| # | Hallazgo | Evidencia |
|---|---|---|
| 1 | Desactivar, borrar o bajar de rol NO corta la sesión: el usuario sigue entrando hasta 7 días, y un superadmin degradado sigue siéndolo | `auth/middleware.py::get_current_user` solo decodifica el JWT (rol del token); `api/auth.py::refresh` reemite el access con el rol del refresh token, sin consultar la base |
| 2 | Se puede quedar sin superadmin: nada impide degradar/desactivar al último, ni a uno mismo | `api/admin/users.py::update_user` sin guardas; la única es `user_id == 1` literal en `delete_user` |
| 3 | Borrar usa `window.confirm`, no la suma; errores tragados | `AdminUsers.jsx::handleDelete` y `.catch(() => {})` en todas las acciones |
| 4 | Nadie cambia su propia contraseña; el reset por admin existe en el backend sin UI ni validación | no hay endpoint "mi cuenta"; `UpdateUserRequest.password` sin reglas |
| 5 | Sin registro de acciones de administración | solo `credential_audit` (credenciales de proveedores) |
| 6 | No se edita el email; `status` acepta cualquier texto | `UpdateUserRequest` |
| 7 | Borrar de verdad deja datos huérfanos o falla | 9 tablas con `user_id`, solo 3 con FK a `jax_users` (`facet_binding.approved_by`, `password_reset_tokens`, `user_api_keys`); memoria, costos y pipelines sin FK |
| 8 | "¿Olvidaste tu contraseña?" nunca envió un correo | `SMTP_*` no definidas en `/etc/jax/.env` ni en el proceso vivo |

## 2. Decisiones (Fernando, 2026-09-12)

- **Alcance:** A contraseñas · B editar y dar de baja · C sesiones reales · D protecciones y registro.
- **Eliminar = dar de baja:** la cuenta queda inutilizable, sale de la lista y libera el
  correo; se conserva el historial. Nada de `DELETE`.
- **Reset por admin = enlace de recuperación por correo**, con SMTP configurado primero.
- **SMTP = servidor propio** `mail.axioma-ia.io`, buzón `no-reply@axioma-ia.io` (ya existe).
- **La configuración SMTP se edita desde Admin, copiando la de AteneaERP** (pedido de Fernando,
  2026-09-12): en la base, no en `/etc/jax/.env`. Fernando la carga desde la pantalla.

## 3. Diseño

### 3.1 Correo saliente (prerrequisito) — copia de AteneaERP

Referencia: `ateneaerp/app/Services/SmtpService.php`, `SmtpController.php` y
`Models/SystemSetting.php` (leídos el 2026-09-12). Se copia su comportamiento, con la
infraestructura que jax-platform ya tiene:

| AteneaERP | jax-platform |
|---|---|
| `system_settings` (clave/valor) | `axioma_config` (clave/valor, ya existe) |
| `Crypt` con la clave de la app | `crypto_secrets.encrypt_secret` / `decrypt_db_secret` (Fernet, `FERNET_KEY`) |
| Pantalla de configuración SMTP | pestaña "Correo (SMTP)" en Admin, solo superadmin |

- **Claves:** `smtp.host`, `smtp.port`, `smtp.encryption` (`tls`/`ssl`/`none`), `smtp.user`,
  `smtp.password` (cifrada), `smtp.from_name`, `smtp.from_email`.
- **Endpoints (superadmin):** `GET /api/admin/smtp` (la contraseña nunca sale: `••••••••` si
  existe), `PUT /api/admin/smtp` (la contraseña solo se reemplaza si llega una nueva distinta
  de la máscara), `POST /api/admin/smtp/test-connection` (saludo, EHLO, STARTTLS, AUTH con
  el error exacto de cada paso, sin enviar), `POST /api/admin/smtp/test` (envía un correo de
  prueba al superadmin; con límite de intentos).
- **Estado corrupto** (la contraseña guardada no descifra, p. ej. rotó `FERNET_KEY`): la
  pantalla lo dice con el motivo, reconfigurar exige volver a escribir la contraseña, y el
  envío queda **deshabilitado con error explícito**. Nunca cae a otra configuración en
  silencio (igual que D-9 de AteneaERP).
- **Sin configurar:** `_send_reset_email` y el reset por admin fallan con código estable
  (503 en el reset por admin; el `forgot-password` público sigue respondiendo neutro).
- **Diferencia deliberada con AteneaERP:** la verificación del certificado TLS queda
  **encendida**. AteneaERP la apaga (`verify_peer => false`) para aceptar autofirmados;
  `mail.axioma-ia.io` tiene un Let's Encrypt `*.axioma-ia.io` válido (medido).
- **Los endpoints genéricos `GET`/`PUT /api/admin/config` excluyen `smtp.*`**: si no, el PUT
  guardaría la contraseña en claro y el GET devolvería el texto cifrado.
- `_send_reset_email` lee esta configuración (sin caché: se envía poco; se lee al enviar).

Medido del lado del servidor: MX `mail.axioma-ia.io` (38.7.24.147), escucha 25/465/587,
STARTTLS en 587 con certificado válido; SPF `a mx ip4:38.7.24.147 include:sendinblue.com
~all`; DKIM `default._domainkey` publicado (RSA 1024); DMARC `p=none`.

**Verificación obligatoria:** "Probar conexión" en verde y un correo de prueba real (con
permiso de Fernando) cuyas cabeceras digan `dkim=pass` y `spf=pass`: que la clave DKIM esté
publicada no prueba que el servidor firme.

### 3.2 C · Sesiones que se cortan de verdad

- Migración idempotente: `jax_users.token_version INT NOT NULL DEFAULT 0`.
- Access y refresh token llevan `tv` (la versión al emitirse).
- `get_current_user` pasa a dependencia async: lee `status, role, token_version` por clave
  primaria. Rechaza 401 si el usuario no existe, no está `active`, o `tv` no coincide. El
  **rol sale de la base**, no del token.
- `/refresh`: la misma verificación, y emite los tokens nuevos con el rol y la versión actuales.
- WebSocket: la misma verificación al conectar.
- `token_version` sube en: cambio de contraseña (propio o por enlace), cambio de rol, cambio
  de estado, baja, y la acción "cerrar sus sesiones".
- Sin caché de entrada (LAS CUATRO §2: sin medición no hay caché). Se mide con `EXPLAIN` y
  una prueba de carga; si hiciera falta un caché, su invalidación es la propia `token_version`.
- Tokens emitidos antes del despliegue (sin `tv`) se tratan como `tv=0`: nadie queda afuera
  al desplegar.

### 3.3 D · Protecciones y registro

- Invariante: **siempre al menos un superadmin `active`**. Rechazan (409, código estable)
  degradar, desactivar o dar de baja al último.
- Nadie puede degradarse, desactivarse ni darse de baja a sí mismo (403). Su contraseña la
  cambia en "Mi cuenta".
- Se elimina `user_id == 1`: lo reemplaza la invariante.
- Tabla `user_admin_audit (id, ts, actor_user_id, target_user_id, action, detail JSON, ip)`,
  índice por `(target_user_id, ts)`. Acciones: `create`, `update_email`, `update_role`,
  `update_status`, `reset_link_sent`, `unlock`, `sessions_revoked`, `baja`,
  `password_changed_self`, `password_reset_completed`. La IP sale de
  `rate_limit.client_ip` (proxy de confianza).
- `GET /api/admin/users/{id}/audit` (últimas 50) y un historial corto en la UI.

### 3.4 A · Contraseñas

- Regla única (backend y frontend): mínimo 8 caracteres, máximo 72 bytes (bcrypt).
- **Mi cuenta:** clic en el correo de `BarraUsuario` → modal con contraseña actual, nueva y
  confirmación (`PasswordInput`). `POST /api/auth/me/password`; exige la actual; con el
  límite de intentos del login. Sube `token_version`: se cierran las otras sesiones y la
  actual recibe tokens nuevos.
- **Reset por admin:** `POST /api/admin/users/{id}/reset-link` reusa
  `_procesar_recuperacion`. Si SMTP no está configurado responde **503 con código
  estable**: el admin ve el error, no un éxito falso. Al completarse el reset
  (`/reset-password`) se sube `token_version`.

### 3.5 B · Editar y dar de baja

- `PUT /api/admin/users/{id}` valida email (formato, 254, único), rol (conjunto cerrado) y
  estado (`active`/`inactive`; `deleted` solo por la baja). Códigos de error estables → i18n.
- **Baja:** `POST /api/admin/users/{id}/baja` → `status='deleted'`, `deleted_at`,
  `deleted_by`; el email pasa a `<original>#baja-<id>-<yyyymmdd>` para liberar la dirección;
  sube `token_version`. `GET /users` no lista los dados de baja. El login ya rechaza lo que no
  es `active`.
- **ConfirmacionSuma** (componente reusable): "Resolvé a + b = ?" con números al azar, el
  botón destructivo se habilita solo con la respuesta correcta. Usado para la baja y
  disponible para todo borrado futuro.
- Acciones de la tabla de usuarios: editar, enviar enlace de recuperación, desbloquear,
  cerrar sesiones, historial, dar de baja. Errores en toasts traducidos, nunca tragados.

## 4. Fuera de alcance

Autoservicio de registro · 2FA · multi-tenant (hoy `tenant_id=1` fijo) · borrar la memoria
de un usuario dado de baja (derecho al olvido): posible ronda propia.

## 5. Verificación (criterios de cierre)

- Tests primero, vistos en rojo: backend (guardas, `token_version` en middleware/refresh/WS,
  baja, auditoría, reglas de contraseña, 503 sin SMTP) y frontend (ConfirmacionSuma, Mi
  cuenta, modal de edición, acciones).
- En vivo: un usuario de prueba desactivado pierde el acceso en el request siguiente; un
  superadmin degradado pierde `/admin` al instante; la baja libera el correo.
- `EXPLAIN` de la consulta del middleware: por `PRIMARY`.
- Prueba de carga de un endpoint autenticado antes y después del cambio, con el número
  escrito en la Biblioteca.
- Correo de prueba con `dkim=pass` y `spf=pass`.
- i18n es/en y modo claro/oscuro en todo lo nuevo.

## 6. Orden de implementación

1. SMTP configurable desde Admin (copia de AteneaERP) + correo de prueba verificado.
2. C · sesiones (migración, middleware, refresh, WS).
3. D · invariantes, guardas y auditoría.
4. A · Mi cuenta y enlace de recuperación.
5. B · edición, baja y ConfirmacionSuma.

Cada etapa: su PR, CI con gate por headSha, despliegue verificado en vivo.
