# Traspaso · secret-scan confiable · 2026-10-22

- **Decisión (Fernando, 2026-10-22):** la Oficina pública debe seguir hasta
  publicación con correo automático; este PR pequeño quita una barrera falsa
  descubierta al validar #229.
- **Hecho (Codex, 2026-10-22):** el workflow anterior `pull_request` ejecutaba
  `scan-pr-secrets.py` y `known-secrets-ci.txt` desde el HEAD que intentaba
  aprobar. Un PR podía alterar cualquiera de los dos. La revisión Sol del SHA
  `0e224cd` también reprodujo omisiones por nombre de archivo y blobs NUL con
  diff textual.
- **Cambio:** `pull_request_target` ejecuta solo código y lista del SHA base
  exacto, con `contents:read` y sin persistir credenciales; trae el HEAD solo
  como objetos Git y verifica su SHA. El token tiene `statuses:write`
  únicamente para poner pendiente y publicar el resultado `scan` en el HEAD
  exacto; GitHub no cuenta el check propio de `pull_request_target` como
  status requerido del PR. Conserva así el contexto requerido `scan`;
  un workflow aparte ejecuta pruebas del scanner propuesto en `pull_request`
  sin secretos. Scanner acotado y pruebas negativas. La lista
  del HEAD no puede perder entradas ya conocidas.
- **Secuencia:** auditoría independiente del SHA exacto de este PR; Fernando
  integra los workflows reservados; el PR bootstrap no genera `scan` nuevo
  desde la base vieja y requiere su integración explícita; rebasar #229 sobre
  el nuevo master; solo el `scan` emitido por `pull_request_target` posterior
  cuenta como barrera de secretos de #229.
- **Pendiente:** #229 sigue sin publicación y requiere CI, auditoría exacta,
  respaldo/restauración, carga y comprobación de DNS/ingreso. El propietario
  `@fjruizhn` está declarado en CODEOWNERS, pero el ruleset todavía no exige
  su revisión: Fernando debe activar `require_code_owner_review` (y el piso de
  aprobación que GitHub exija) para cambios de esta barrera antes de tratarla
  como control autónomo contra colaboradores con escritura. El ruleset vigente
  también tiene `strict_required_status_checks_policy=false`: se debe activar
  `true` al integrar para que un `scan` verde de una base vieja no apruebe un
  HEAD contra `master` nuevo. Antes de sumar writers, hará falta un emisor de
  status dedicado que GitHub distinga de los workflows que ellos puedan crear.
- **Carreras y logs:** el workflow cancela la corrida vieja por PR, compara
  HEAD/base actuales antes de publicar success y mantiene `scan` pendiente o
  en failure si cambia algo. Checkout está fijado al commit 11bd719… de v4.2.2.
  El escáner anota un digest de ruta en vez de imprimir nombres que puedan
  contener el secreto o caracteres de control.
- **Alternativa descartada:** declarar válido el scan del HEAD de #229; el
  contenido que se revisa podría cambiar el revisor y la referencia.
