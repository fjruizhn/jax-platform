# Plan de migración MariaDB 11.8 → 12.3 LTS (Docker, blue/green)

**Fecha:** 2026-08-09 · **Rama:** `infra/mariadb-12.3-migration` · **Estado:** A1 — plan en papel, sin ejecutar.
**Base factual:** `docs/auditoria-api-keys-2026-08-09.md` + verificaciones A0 (PARADA 1, mismo día). No se repite lo ya verificado, solo se referencia.

---

## 0. Decisiones tomadas en A0 que fijan este plan

- **Puerto del contenedor: `3308`**, verificado libre en hall9000 (3306 = 11.8 nativa, 3307 conceptualmente asociado a atemai-net aunque libre localmente — se evita para no confundir runbooks).
- **Volumen: `/var/lib/mariadb-12.3-docker/data`** (bind mount, ruta real). `/var` tiene 133GB libres; `/` está al 91%, descartado.
- **Imagen: `mariadb:12.3.2`**, confirmada existente en Docker Hub oficial (digest `sha256:c40f9a0d...`, amd64, publicada 2026-08-04). Pinneada — nunca `:latest` (en el esquema de versionado nuevo, `:latest` puede saltar a 13.0 rolling release).
- **Docker CE 29.7.2 ya instalado y verificado** (hello-world OK). `sudo docker` explícito en todo — `fruiz` no está en el grupo `docker`.
- **UFW activo, default deny — R1 cerrado.** El bind a loopback del compose es defensa en profundidad, no un parche de un firewall roto.

## 1. docker-compose propuesto

Archivo: `ops/mariadb-12.3/docker-compose.yml` (ya escrito en esta rama, no ejecutado).

Puntos no negociables cumplidos:
- Imagen pinneada a `12.3.2`, no `:latest`.
- Puerto publicado como `"127.0.0.1:3308:3306"` — bind explícito a loopback. **Criterio de verificación**: tras levantar el contenedor, `ss -tlnp | grep 3308` debe mostrar `127.0.0.1:3308`, nunca `0.0.0.0:3308`. Si aparece `0.0.0.0`, es un fallo de configuración y no se avanza al cutover.
- Volumen bind-mount a `/var/lib/mariadb-12.3-docker/data`, no volumen anónimo.
- `character-set-server=utf8mb4` + `collation-server=utf8mb4_uca1400_ai_ci` a nivel servidor (default de 11.8 para las tablas nuevas). Las tablas viejas con `utf8mb4_unicode_ci` (`messages`, `jacobs_*`, `facts`, etc.) llevan su collation explícita en el propio `CREATE TABLE` de cada una — el dump lógico la preserva por tabla independientemente del default del servidor; se verifica en la paridad (sección 4), no se asume.
- Red Docker dedicada con **subred fija `172.30.5.0/24`**, no la red default dinámica de Docker — necesario para que los grants de usuario tengan un patrón de host determinista y no se rompan si se agregan más contenedores al host en el futuro.
- `MARIADB_AUTO_UPGRADE=1`: activa el auto-upgrade de tablas de sistema del entrypoint oficial — no debería ser necesario en nuestro caso (ver sección 2), pero se deja activo como red de seguridad sin costo.
- `transaction-isolation=REPEATABLE-READ` explícito (mismo nivel que 11.8 por default) — no se cambia nada de aislamiento en este movimiento; el cambio de comportamiento (A0.9) viene de `innodb_snapshot_isolation` pasando a ON por default en 12.3, que es independiente del nivel de aislamiento configurado y no se puede "revertir" sin perder la corrección que ese cambio introduce. No se desactiva.

## 2. Método de copia y su justificación

**Decisión: dump lógico (`mariadb-dump`), NO backup físico (`mariadb-backup`).**

Se investigó específicamente si `mariadb-backup` (copia binaria de los archivos InnoDB) sería más seguro para las columnas VECTOR, ya que evitaría por completo la re-serialización a texto. Se descartó por lo siguiente, verificado documentalmente:

- La documentación oficial de MariaDB para upgrades entre versiones LTS mayores describe el proceso soportado como **in-place, mismo datadir, mismo host**: detener el servidor, reemplazar binarios, reiniciar sobre el mismo datadir, correr `mariadb-upgrade`. No hay guía oficial que confirme `mariadb-backup` como método soportado para restaurar un datadir de 11.8 en una instancia 12.3 fresca en otro proceso/contenedor — que es exactamente nuestro escenario (blue/green, dos instancias corriendo en paralelo). Usar `mariadb-backup` así sería *tan* no documentado como el riesgo de VECTOR que se quiere evitar, solo que en el terreno del formato binario de página InnoDB en vez del terreno de VECTOR.
- El dump lógico es el método tradicionalmente portable entre versiones mayores de MariaDB/MySQL — es la razón por la que existe.
- El dataset es pequeño: `jax_memory` (10.44MB) + `jax_memory_test` (0.22MB). La desventaja clásica de `mariadb-dump` (lentitud en datasets grandes) no aplica aquí — la velocidad no es un factor de decisión.
- Con dump lógico, el contenedor 12.3 inicializa su **propio** esquema `mysql` (tablas de sistema) nativo de 12.3 desde cero, vía el entrypoint oficial de la imagen (`mariadb-install-db`). No se importa el `mysql` schema de la 11.8 — solo se importan `jax_memory` y `jax_memory_test` (bases de usuario). Esto significa que, a diferencia de un upgrade in-place, **no hace falta correr `mariadb-upgrade` para arreglar tablas de sistema viejas**, porque nunca se copian tablas de sistema viejas. `MARIADB_AUTO_UPGRADE=1` queda como red de seguridad, no como paso crítico.

**Mitigación específica para VECTOR (respuesta a A0.3):** el dump lógico estándar genera `CREATE TABLE` (con el índice `VECTOR KEY` incluido) seguido de los `INSERT`. Esto inserta fila por fila contra una tabla ya indexada — exactamente el patrón que la guía oficial de MariaDB Vector señala como más lento (no incorrecto documentalmente, pero tampoco el patrón recomendado, y es el terreno menos probado). Se sigue un procedimiento de tres fases para `messages` y `facts` (las dos únicas tablas con columnas VECTOR):

1. **Schema sin índice vectorial**: `mariadb-dump --no-data jax_memory` → editar el DDL resultante para **quitar** la cláusula `VECTOR KEY idx_embedding (embedding) DISTANCE=cosine` de `messages` y `facts` (se documenta la cláusula exacta a remover, tomada del DDL real capturado en la auditoría previa). El resto del DDL (incluida la columna `embedding VECTOR(768)` en sí, su `DEFAULT VEC_FromText(...)`, y todos los demás índices) se mantiene intacto.
2. **Carga de datos**: `mariadb-dump --no-create-info jax_memory` → restaurar contra las tablas ya creadas (sin índice vectorial) en 12.3. Esto es una carga de INSERT puros contra tablas sin el índice ANN — el escenario exacto que el tip oficial de performance recomienda.
3. **Construcción del índice**: `ALTER TABLE messages ADD VECTOR KEY idx_embedding (embedding) DISTANCE=cosine;` y lo mismo para `facts`, **una sola vez**, sobre los datos ya cargados — construcción en bloque, no incremental.

Este procedimiento no es solo una optimización de velocidad: al evitar el path de "escribir el índice VECTOR desde el DDL del dump completo, con todas las filas insertándose contra un índice ya vivo", se reduce la superficie del riesgo declarado incierto en A0.3 a un solo paso bien acotado y verificable (`ALTER TABLE ADD VECTOR KEY`), que se valida directamente con la prueba de la sección 3.

`jax_memory_test` (sin columnas VECTOR, confirmado en A0.1) se migra con `mariadb-dump` estándar de un solo paso, sin necesidad de este procedimiento.

## 3. Prueba de integridad de vectores (criterio de aceptación, no opcional)

Sobre una copia de prueba (no la migración real todavía — esto se ejecuta en A2 antes de proponer el cutover):

1. En 11.8: capturar `id` y resultado de `VEC_DISTANCE_COSINE(embedding, @ref)` para una consulta de referencia fija, ordenado, sobre las 620 filas de `messages` y las 67 de `facts`. `@ref` = el embedding de una fila fija elegida por `id` (para que la prueba sea determinista y repetible), ej. `messages.id = 887` (la más reciente confirmada en la sesión de hoy).
2. Ejecutar el mismo query, mismo `@ref` (mismo vector, no un vector nuevo generado por Ollama — se inserta el mismo array de floats capturado en el paso 1 vía `VEC_FromText`), contra 12.3 tras la carga en 3 fases de la sección 2.
3. **Criterio de aceptación**: incluye 3 chequeos, MariaDB en instancia
   - Comparación de resultado: los `id` devueltos, en el mismo orden, con la distancia coseno idéntica hasta la precisión de float reportada por `VEC_DISTANCE_COSINE` (se acepta diferencia solo por redondeo de punto flotante, no por vecino distinto).
   - `EXPLAIN` de la misma consulta en 12.3 debe mostrar `key: idx_embedding` (uso real del índice ANN, no full scan) — replica la verificación que ya se hizo manualmente sobre 11.8 al diagnosticar el gap de Fernet.
   - Conteo de filas con embedding "no-cero" (`VEC_ToText(embedding) <> VEC_ToText(VEC_FromText('[0.0]'))`, mismo patrón usado en el diagnóstico previo) debe coincidir exactamente entre 11.8 y 12.3 para `messages` y `facts`.
4. Si cualquiera de los 3 falla, la migración **no avanza** a cutover — se investiga el fallo puntual (posiblemente ajustando el procedimiento de 3 fases) antes de repetir la prueba.

## 4. Criterios de paridad (completos, no solo vectores)

Para `jax_memory` y `jax_memory_test`, comparando 11.8 (origen) vs 12.3 (destino, tras la carga):

- **Conteo de filas** por tabla (`SELECT COUNT(*)` de las 20+8 tablas) — debe coincidir exacto.
- **Checksum** por tabla: `CHECKSUM TABLE <tabla>` en ambas instancias (MariaDB soporta esto nativamente; nota: el algoritmo de checksum puede diferir por collation si cambia el orden de comparación de cadenas — se verifica que la collation por tabla sea idéntica primero, sección 1, antes de confiar en el checksum).
- **Grants**: recrear `jax_user` y `backup_user` en 12.3 con el host `172.30.5.%` (subred fija del compose) en vez de `localhost` — el gotcha de A0.5. Los privilegios exactos de `backup_user` no se pudieron leer en A0 (sin acceso root de MariaDB); se capturan con `SHOW GRANTS FOR backup_user@localhost` en 11.8 durante A2, con acceso root puntual, antes de recrear el usuario en 12.3 — no se asume, se copia el privilegio real.
- **Índices**: `SHOW INDEX FROM <tabla>` para cada tabla con índices no triviales (`user_api_keys.uk_user_provider`, `facet_models.uk_facet_model`, `facts.ft_fact_text` FULLTEXT, `messages.idx_embedding`/`facts.idx_embedding` VECTOR KEY) — comparar cantidad y tipo, no solo nombre.
- **Prueba de vectores** de la sección 3 — obligatoria, no se sustituye por los checksums (un checksum de fila puede coincidir en bytes crudos del VECTOR sin que el índice ANN se haya reconstruido correctamente encima).

## 5. Secuencia de cutover (con ventana)

**No hay ventana de silencio sin escrituras perdidas — este orden es obligatorio:**

1. **[con 11.8 sirviendo, sin tocar nada]** A2 ya completado: 12.3 corriendo en paralelo, con una copia de datos de una corrida previa de prueba (no la final).
2. **Aviso explícito antes de este paso** (regla dura de la corrida): detener los 4 servicios JAX en este orden — `jax-platform-frontend` (no accede a DB, se detiene primero solo para no mostrar UI con backend caído), `jax-platform`, `jax-las-manos`, y confirmar que `jax-memory-worker.timer` no tiene una corrida en curso (`systemctl status jax-memory-worker.service` debe estar `inactive`, no `activating`) antes de continuar — si hay una corrida en curso, se espera a que termine (máx. unos segundos, es un `oneshot`) en vez de matarla a mitad de un `INSERT`.
3. **Copia delta final**: repetir el dump de 3 fases de la sección 2, esta vez como la copia definitiva — captura el estado de `jax_memory`/`jax_memory_test` en el instante exacto en que los consumidores ya están detenidos, sin más escrituras posibles.
4. **Verificación de paridad** (sección 4) sobre esta copia delta final — no la de prueba.
5. **Recableo** (checklist sección 7) — variables de entorno, y los 3 archivos de código con puerto hardcodeado/implícito identificados en A0.2.
6. **Arranque en orden inverso**: `jax-las-manos` → `jax-platform` → `jax-platform-frontend`.
7. **Verificación end-to-end** (compuerta A4) antes de declarar el cutover exitoso.
8. Solo entonces: **apagar** (`systemctl stop mariadb`, NUNCA `disable` ni desinstalar) la 11.8 nativa.

Tiempo estimado de la ventana (pasos 2-6): con un dataset de 10MB, el dump/restore de 3 fases toma segundos; el recableo de código (edición de 3 archivos + reinicio de 3 servicios) es el paso más largo. Se estima una ventana de **menos de 5 minutos**, pero se mide en A2/A3 con evidencia real, no se asume.

## 6. Plan de rollback

Si cualquier verificación de la compuerta A4 falla después del cutover:

1. **Revertir el recableo**: los 3 archivos de código editados vuelven a su versión anterior (`git checkout -- <archivo>` sobre la rama, ya que están versionados) o al puerto 3306 si se optó por una variable de entorno `JAX_DB_PORT` en vez de editar código (ver checklist, sección 7 — la implementación concreta decide esto en A2/A3, este plan deja ambas opciones abiertas).
2. **Reiniciar los 4 servicios** apuntando de nuevo a 3306 (11.8, que nunca se detuvo del todo si el rollback ocurre antes del paso 8 de la sección 5 — sigue corriendo en paralelo hasta que el cutover se declare exitoso).
3. Si el rollback ocurre **después** de haber apagado la 11.8 (paso 8): se reinicia con `systemctl start mariadb` — el datadir nativo (`/var/lib/mariadb/`) nunca se tocó, nunca se borró, sigue intacto con los datos de antes del cutover más cualquier escritura que haya ocurrido si el corte de consumidores (paso 2 de la sección 5) no fue completo. Se investiga esa brecha específicamente antes de reabrir a producción.
4. **Tiempo estimado de rollback**: minutos — es esencialmente el mismo procedimiento de recableo en reversa, sin necesidad de restaurar ningún backup (la 11.8 nunca perdió sus datos).
5. El contenedor 12.3 se detiene (`sudo docker compose down`, sin `-v` — nunca se borra el volumen bind-mounted) para investigar la causa del fallo con calma, sin presión de producción caída.

## 7. Checklist de recableo

- [ ] `/etc/jax/.env`: si se decide introducir `JAX_DB_PORT`, agregar aquí con valor `3308`. (Decisión de implementación pendiente para A2/A3: variable nueva vs. hardcodear 3308 en los 3 sitios — se recomienda la variable, coherente con cómo ya se leen `JAX_DB_HOST`/`USER`/`PASSWORD`/`NAME`.)
- [ ] `jax-platform/backend/db/connection.py:12` — reemplazar el `port=3306` hardcodeado.
- [ ] `jax/jax/memory/db.py:88` — actualizar el default de la firma de `connect()`, y los 4 call-sites que no pasan `port` explícito (`jax-platform/backend/api/chat.py:70-78`, `jax/jax/core/main.py:367-371`, `jax/jax/memory/worker.py:185-189`, `jax/jax/memory/embedding_worker.py`).
- [ ] `jax/jacobs/store.py:19-27` (`_db_cfg()`) — agregar `port` a la config de `aiomysql.connect()`.
- [ ] **`/etc/restic/mysql-backup-local.cnf`** — agregar `host=127.0.0.1` y `port=3308` explícitos. Sin esto, el backup diario sigue respaldando la 11.8 vieja en silencio (A0.8). Es el ítem de mayor riesgo de fallo silencioso de todo el checklist.
- [ ] `jax-platform/backend/api/admin/dashboard.py:69` — actualizar la etiqueta de puerto hardcodeada en el panel admin (cosmético, no funcional, pero queda desactualizado si no se toca).
- [ ] Grants: crear `jax_user` y `backup_user` en 12.3 con host `172.30.5.%`, no `localhost`.
- [ ] `jax/jax/memory/embedding_worker.py`: **decisión pendiente de tu confirmación** — no está en ningún timer/cron (A0.2), así que técnicamente seguiría funcionando si se actualiza `.env`/`JAX_DB_PORT` igual que los demás (usa el mismo mecanismo), pero al ser invocación manual, nadie lo va a correr en la ventana de cutover — se recomienda solo confirmar que lee las mismas variables (ya confirmado) y no requiere acción extra más allá de lo ya cubierto arriba.
- [ ] `jax-platform/backend/api/admin/keys.py` y cualquier lugar que escriba `_write_env_key` a `/etc/jax/.env`: confirmar que no pisan la nueva variable `JAX_DB_PORT` si se agrega (no debería, esa función solo toca las API keys de proveedor, pero se verifica en A2).
- [ ] Confirmar con `ss -tlnp` que `jax-platform`, `jax-las-manos` y `jax-memory-worker` (durante su corrida) abren conexión hacia `127.0.0.1:3308`, no `3306`, tras el recableo.

---

**Fin de A1.** Este documento no ejecuta nada — es la propuesta para PARADA 2. Ningún comando de esta sección se ha corrido; el `docker-compose.yml` referenciado existe en disco pero no se ha invocado `docker compose up`.
