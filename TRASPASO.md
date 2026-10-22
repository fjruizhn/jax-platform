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
  exacto, con token read-only y sin persistir credenciales; trae el HEAD solo
  como objetos Git y verifica su SHA. Pruebas del scanner propuesto corren en
  `pull_request` sin secretos. Scanner acotado y pruebas negativas. La lista
  del HEAD no puede perder entradas ya conocidas.
- **Secuencia:** auditoría independiente del SHA exacto de este PR; Fernando
  integra el workflow reservado; rebasar #229 sobre el nuevo master; solo la
  corrida `trusted-scan` posterior cuenta como barrera de secretos de #229.
- **Pendiente:** #229 sigue sin publicación y requiere CI, auditoría exacta,
  respaldo/restauración, carga y comprobación de DNS/ingreso.
- **Alternativa descartada:** declarar válido el scan del HEAD de #229; el
  contenido que se revisa podría cambiar el revisor y la referencia.
