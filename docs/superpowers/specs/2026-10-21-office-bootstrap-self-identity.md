# Bootstrap Office con identidad propia verificada

2026-10-21 · DECISIÓN técnica Codex, análisis Sol delivery_path sobre el contrato ya decidido por Fernando. Prerrequisito de Task3C; no ejecución productiva ni cuenta creada. Objetivo humano ya elegido: Fernando indicó su correo Axioma; se resuelve un user_id canónico explícito, no se autoriza por email ni por rol clásico. La inspección histórica de ese correo devolvió cero usuarios; debe repetirse en activación.

## Camino canónico

1. Un AccountsAdmin existente crea el usuario canónico en jax_users por su API actual, sin signup ni almacén paralelo.
2. El sujeto establece su contraseña mediante bearer de recuperación canónico, desconocida por el administrador.
3. El sujeto enrola su MFA real mediante Task3B y obtiene sesión autenticada con prueba actual reciente.
4. CLI `bootstrap-office-root --target-user-id N --expected-email <runtime> --basis-ref <runtime> --access-token-stdin` consume su token por stdin. Nunca recibe actor_user_id ni proof_event_id libres, ni secretos en argv/logs.

La CLI verifica JWT y sesión en DB; deriva actor y referencia de prueba. Actor debe ser target, usuario/tenant activos, staff INTERNAL u OFFICE_INTERNAL, factor activo y prueba durable actual, reciente y no futura. expected-email solo comprueba identidad escogida por el humano, jamás selecciona/autorización por nombre/email.

El sujeto debe haber fijado credencial propia después del último create/update_email/password_set_by_admin del usuario. Se usa user_admin_audit existente: evento self password_reset_completed posterior al último control administrativo; un password_changed_self posterior conserva evidencia; una contraseña/email administrativo posterior la invalida. Evaluar bajo lock del usuario. No fabricar eventos de historial para pasar la ceremonia. user_admin_audit.id es BIGINT firmado; preservar tipo en FK. Toda consulta nueva tiene EXPLAIN sobre SQL real.

## Transacción y procedencia

Una transacción bloquea ceremonia fija INITIAL_OFFICE_SUPERUSER, comprueba perfil previo ausente, crea binding Office permanente y perfil OFFICE_SUPERUSER, invalida reset tokens clásicos pendientes, sube token_version y guarda procedencia inmutable. Cortar conexiones después de commit. No fabricar MFA_PROOF, AUTHORIZATION ni SYSTEM_FENCE. Si existe perfil previo incompatible, falla cerrado.

office_bootstrap_state registra ceremony_key PKascii, target_user_id FK, identity_actor_user_id FK, identity_proof_event_id FK al ledger MFA, credential_audit_id FK a user_admin_audit.id, basis_ref ASCII explícito, completed_at DB UTC. CHECK actor=target y campos presentes; triggers append-only. No índice singleton sobre perfiles OfficeRoot. Misma ceremonia/actor/target/fundamento es replay idempotente; otro objetivo o fundamento no reinicia. Una ceremonia de recuperación de todos los roots es otro contrato, no reusar bootstrap.

La primera comprobación de replay necesita respetar sesión y token_version posteriores a la ceremonia: no permitir que un token inválido convierta replay en autoridad ni exigir que un token precorte siga siendo válido. Probar el contrato con sesión actual del mismo sujeto y recibo/procedencia exactos; si se necesita recuperar el resultado tras ack perdido, debe bastar lectura autenticada actual sin rehacer grant ni segundo bump. Esta precisión no habilita un bypass de sesión.

## Validación

Crear usuarios sintéticos solo en DB aislada: admin clásico nunca se vuelve OfficeRoot por inferencia; token ajeno/forjado/stale, perfil previo, actor distinto, factor ausente/proof stale/futuro/version errónea, credencial conocida por admin y cambios administrativos posteriores deniegan. Dos ceremonias concurrentes un ganador; replay exacto sin otro grant/bump; otro target/fundamento falla. Fallo de insert de procedencia revierte perfil, tokens y versiones. Probar que no hay singleton de roots y que las promociones posteriores usan API root+MFA. No usar JWT/OTP inventados como identidad de Fernando.
