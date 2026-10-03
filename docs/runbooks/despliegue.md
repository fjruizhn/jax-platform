# Despliegue de JAX / Axioma Platform

> Escrito el 2026-09-20, ejecutándolo. Cada paso y cada trampa de acá se
> verificaron en vivo desplegando la pantalla de Memoria. **No había runbook**:
> el paso del sitio público ya se había saltado antes sin que nadie se enterara,
> porque el `:5173` local se ve perfecto mientras el público sigue viejo.

## Lo primero: son TRES máquinas, no una

| Qué | Dónde | Sirve |
|---|---|---|
| Backend | `hall9000:/srv/jax-prod/jax-platform/backend` | `0.0.0.0:8080` (uvicorn) |
| Núcleo + LAS MANOS | `hall9000:/srv/jax-prod/jax` | `:7777`, y **la migración del esquema** |
| Frontend interno | `hall9000:/srv/jax-prod/jax-platform/frontend` | `:5173` (`vite dev`) |
| **Sitio público** | **`atem-ai:/www/wwwroot/axioma-ia.io`** | `https://axioma-ia.io` (nginx de aaPanel) |

`axioma-ia.io` sirve un **bundle compilado** y proxea `/api` y `/ws` a
`172.16.20.5:8080`. **El `:5173` de hall9000 NO es lo que ve un cliente**: es un
`vite dev` interno. Actualizar solo el `:5173` deja el sitio público viejo y
todo parece bien desde adentro.

## ⚠️ El orden es obligatorio y tiene una sola dirección

**`jax` va SIEMPRE antes que `jax-platform`.** La plataforma importa `MemoryDB`
del checkout de producción de jax (`JAX_REPO_PATH=/srv/jax-prod/jax`), y las
columnas nuevas solo aparecen cuando la migración corre **desde ahí**. Al revés,
los endpoints revientan con `Unknown column` la primera vez que se usan.

> Ojo, no confundir: el orden de **merge** de los PRs de *aislamiento de bases*
> es el opuesto (jax-platform primero, porque el `mirror-sync` de jax clona su
> master). Son dos órdenes distintos por dos razones distintas.

---

## 0 · Respaldo, y probarlo (Principio VI)

> **Corregido el 2026-09-27 (Hyde), ejecutándolo en el despliegue del catálogo
> de modelos.** La versión anterior de este paso tenía dos defectos:
>
> 1. **La contraseña en el argv** (`-p"$JAX_DB_PASSWORD"`): mientras corre el
>    dump queda legible por cualquier usuario del host en `/proc/<pid>/cmdline`
>    (`/proc` no tiene `hidepid` en hall9000). Ahora va en un archivo de
>    opciones `600` de root que se borra al salir.
> 2. **El respaldo NO se podía restaurar con el usuario de la aplicación.** Los
>    triggers se vuelcan con `DEFINER=root@localhost` y restaurarlos exige el
>    privilegio `SET USER`: la restauración de prueba fallaba en la línea 9054
>    con `ERROR 1227`. O sea, el paso decía «probá la restauración» y la
>    restauración no funcionaba. Ahora se quitan las cláusulas `DEFINER` al
>    restaurar (el trigger queda con el usuario que restaura).
>
> Medido con esta versión: dump de 11 MB, restauración con `facts`, `model`,
> `facet_binding`, `memory_revisions`, `memory_objects` y `axioma_usage`
> idénticos a producción, 66 tablas y 5 triggers en los dos lados.

```bash
D=~/respaldos-despliegue/$(date +%Y-%m-%d)-<motivo>; mkdir -p $D && chmod 700 $D

sudo bash -c '
set -euo pipefail; set -a; . /etc/jax/.env; set +a
D='"$D"'
T=$(mktemp -d); chmod 700 $T; trap "rm -rf $T" EXIT
printf "[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\n" \
  "$JAX_DB_HOST" "$JAX_DB_PORT" "$JAX_DB_USER" "$JAX_DB_PASSWORD" > $T/c.cnf
chmod 600 $T/c.cnf

# Los SHA a los que volver, ANTES de tocar nada
{ echo "fecha: $(date -Is)"
  echo "jax prod:          $(git -c safe.directory=/srv/jax-prod/jax -C /srv/jax-prod/jax rev-parse HEAD)"
  echo "jax-platform prod: $(git -c safe.directory=/srv/jax-prod/jax-platform -C /srv/jax-prod/jax-platform rev-parse HEAD)"
  echo "bundle publico:    $(curl -s https://axioma-ia.io | grep -oE "assets/index-[^\"]*\.js" | head -1)"
} > $D/ESTADO-ANTES.txt

mariadb-dump --defaults-extra-file=$T/c.cnf --single-transaction --routines --triggers \
  jax_memory | gzip > $D/jax_memory.sql.gz
chown -R fruiz:fruiz $D; chmod 600 $D/*'
```

**Y restauralo en una base descartable antes de seguir** — un respaldo sin
restauración probada no es un respaldo:

```bash
sudo bash -c '
set -euo pipefail; set -a; . /etc/jax/.env; set +a
T=$(mktemp -d); chmod 700 $T; trap "rm -rf $T" EXIT
printf "[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\n" \
  "$JAX_DB_HOST" "$JAX_DB_PORT" "$JAX_DB_USER" "$JAX_DB_PASSWORD" > $T/c.cnf
chmod 600 $T/c.cnf; C="--defaults-extra-file=$T/c.cnf"
B=jax_memory_test_restauracion_$(date +%Y%m%d)
mariadb $C -e "DROP DATABASE IF EXISTS \`$B\`; CREATE DATABASE \`$B\`;"
# Quitar DEFINER: sin esto falla con ERROR 1227 (hace falta SET USER).
gunzip -c '"$D"'/jax_memory.sql.gz | sed -e "/^CREATE DATABASE/d" \
  -e "s/^USE \`jax_memory\`;/USE \`$B\`;/" \
  -e "s#/\*!50017 DEFINER=[^*]*\*/##g" -e "s/DEFINER=\`[^\`]*\`@\`[^\`]*\`//g" \
  | mariadb $C "$B"
for t in facts model facet_binding memory_revisions memory_objects axioma_usage; do
  echo "$t prod=$(mariadb $C -N -e "SELECT COUNT(*) FROM jax_memory.$t") restaurada=$(mariadb $C -N -e "SELECT COUNT(*) FROM \`$B\`.$t")"
done
mariadb $C -e "DROP DATABASE \`$B\`;"'
```

Cada par tiene que dar el mismo número. Si alguno difiere, **no se sigue**.

> No uses `--defaults-extra-file=<(printf ...)` para ahorrarte el archivo
> temporal: un descriptor de sustitución de proceso se lee **una sola vez**, y
> desde la segunda llamada `mariadb` cae al socket local por defecto
> (`ERROR 2002 ... mysqld.sock`). Medido el mismo día.

## 1 · `jax` (si el cambio lo toca)

```bash
cd /srv/jax-prod/jax && git fetch origin && git merge --ff-only origin/master
sudo systemctl restart jax-las-manos
```

La migración corre sola al arrancar (`ensure_schema`). **Comprobar la columna en
la base, no suponerla:**

```sql
SHOW COLUMNS FROM jax_memory.facts LIKE 'verified_by';
```

## 2 · Backend de la plataforma

> **Si el cambio toca el ejecutor del catálogo o el nombre de su candado, NO sigas
> acá:** usa «Caso general: un cambio toca el nombre del candado o el ejecutor
> programado», más abajo.

```bash
cd /srv/jax-prod/jax-platform && git fetch origin && git merge --ff-only origin/master
sudo systemctl restart jax-platform
```

Verificar **por comportamiento**, no por `is-active`:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/health   # 200
# un endpoint NUEVO sin token: 401 = existe y exige auth; 404 = no se desplegó
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/api/admin/memoria/hechos
```

## 3 · Frontend interno (`:5173`)

```bash
sudo systemctl restart jax-platform-frontend
```

Es `vite dev`: toma el código sin compilar. **Esto NO actualiza el sitio
público.**

## 4 · El sitio público — el paso que se olvida

```bash
# Compilar DESDE el checkout de produccion (frontend/dist/ esta en .gitignore,
# asi que no ensucia el checkout ni desarma el freno del ExecStartPre)
cd /srv/jax-prod/jax-platform/frontend
export PATH=/home/fruiz/.nvm/versions/node/v24.16.0/bin:$PATH
npm run build

# Respaldar el sitio actual en atem-ai
ssh -p 58291 fruiz@172.16.20.11 'R=/www/wwwroot/axioma-ia.io; \
  B=~/respaldos-sitio/axioma-$(date +%Y%m%d-%H%M%S); mkdir -p "$B"; sudo cp -a "$R"/. "$B"/'

# Publicar
tar -czf - -C dist . | ssh -p 58291 fruiz@172.16.20.11 \
  'T=$(mktemp -d) && tar -xzf - -C "$T" && sudo cp -a "$T"/. /www/wwwroot/axioma-ia.io/ \
   && sudo chown -R www:www /www/wwwroot/axioma-ia.io/{assets,index.html,favicon.svg}; rm -rf "$T"'
```

## 5 · Verificar DESDE AFUERA

Desde adentro todo se ve bien aunque el sitio público esté viejo. El control es
que **el hash del bundle haya cambiado**:

```bash
curl -s https://axioma-ia.io | grep -oE 'assets/index-[^"]*\.js'   # != ESTADO-ANTES.txt
curl -s -o /dev/null -w "%{http_code}\n" https://axioma-ia.io/api/health
```

---

## Las trampas, todas medidas

- **Los dos checkouts tienen DUEÑOS DISTINTOS, y eso cambia el comando.**
  Medido el 2026-09-20:

  | checkout | dueño | cómo se actualiza |
  |---|---|---|
  | `/srv/jax-prod/jax-platform` | `fruiz` | `git` normal, **sin sudo** |
  | `/srv/jax-prod/jax` | **`jaxsvc`** | ver abajo |

  En el de `jax` **nadie puede hacer `fetch` solo**: `jaxsvc` y `root` no tienen
  llave de GitHub, y el remoto es SSH. Falla con `Permission denied (publickey)`.
  La única vía es prestarle la llave de `fruiz`:

  ```bash
  sudo GIT_SSH_COMMAND="ssh -i /home/fruiz/.ssh/id_ed25519 -o IdentitiesOnly=yes" \
    git -C /srv/jax-prod/jax -c safe.directory=/srv/jax-prod/jax pull --ff-only origin master
  ```

- **⚠️ Después de ese pull hay que DEVOLVER EL DUEÑO.** Correr como root deja
  archivos de `root` dentro de un repo de `jaxsvc`. Medido en el despliegue del
  2026-09-20: **45 objetos en `.git` y 6 en el árbol de trabajo**, y entre esos
  seis estaba **`jax/memory/db.py` — el archivo que el servicio lee**. El
  servicio arrancó igual esa vez, pero el siguiente reinicio podía fallar por
  permisos con un error que no menciona nada de esto.

  ```bash
  sudo chown -R jaxsvc:jaxsvc /srv/jax-prod/jax
  sudo find /srv/jax-prod/jax -user root | wc -l   # tiene que dar 0
  ```

- **`sudo git` sobre `/srv/jax-prod/*` falla y engaña.** Los checkouts son de
  `fruiz`: como root da `dubious ownership`, y peor — el `fetch` falla con
  `Permission denied (publickey)` (el remoto es SSH y root no tiene la llave)
  mientras el `merge --ff-only` siguiente dice **«Already up to date»** contra
  una referencia vieja. Sale 0 y no desplegó nada. **Corré git como `fruiz`,
  sin sudo**, y **comprobá el SHA después**, no el mensaje.
- **`ExecStartPre` se niega a arrancar** si el checkout no está limpio y en
  master (`jax-checkout-de-produccion-sano.sh`). Que el `merge` sea `--ff-only`
  y que no queden archivos sueltos.
- **El bundle viejo sigue sirviéndose** tras publicar: se copia encima sin
  borrar. Es deliberado (no rompe sesiones en vuelo) pero **acumula**: había 14
  archivos en `assets/` el 2026-09-20. Limpiar cada tanto, nunca durante un
  despliegue.
- **`is-active` no es «funciona».** Un servicio puede estar `active` sirviendo
  código viejo. Verificar comportamiento: un endpoint nuevo que devuelva 401 y
  no 404, y el hash del bundle.

## Caso concreto: descartar/recuperar/ocultar pipelines (Task 4, 2026-09-22)

La regla general de arriba ("`jax` va SIEMPRE antes que `jax-platform`") acá
no es una buena práctica -- es un **hard fail**. `SQL_PIPELINES_DEL_USUARIO`
(`GET /api/pipelines`, la lista principal) lleva
`FORCE INDEX (idx_pipelines_visibles)`: si ese índice no existe todavía,
MariaDB devuelve el error 1176 y el endpoint responde **500**, no un plan
peor ni una degradación silenciosa.

**Orden exacto, sin margen:**

1. **`jax` primero, con jax#257 (descartar-pipelines) MÁS jax#259 (la
   columna `visible`/`idx_pipelines_visibles`)** -- verificar que los DOS
   estén en el `master` que se despliega antes de arrancar este paso: que
   un PR esté mergeado no alcanza, tiene que estar en el checkout que se
   va a poner en producción. Reiniciar `jax-las-manos`
   (`sudo systemctl restart jax-las-manos`, mismo comando que el paso 1 de
   arriba): el `init_tables()` de `jax` corre al arrancar el proceso y crea
   `status_previo`/`descartado_por`/`descartado_at`/`visible` y los cuatro
   índices nuevos (`idx_pipelines_descartados`, `idx_pipelines_ocultos`,
   `idx_pipelines_visibles`, más los que ya trajo Task 3). Comprobar en la
   base, no suponerlo:

   ```sql
   SHOW COLUMNS FROM jax_memory.jacobs_pipelines LIKE 'visible';
   SHOW INDEX FROM jax_memory.jacobs_pipelines WHERE Key_name = 'idx_pipelines_visibles';
   ```

   **El camino feliz de arriba no es el único.** `init_tables()` (jax,
   `jacobs/store.py::_agregar_columna_acotada`) espera hasta 30 s por el
   metadata lock de cada `ADD COLUMN`/`CREATE INDEX`, y las columnas
   CONTRATO **fallan CERRADO**: si otra transacción tiene `jacobs_pipelines`
   tomada y la espera vence (error de MariaDB **1205**), `init_tables()`
   levanta una excepción y **LAS MANOS no llega a arrancar** -- no es que
   el DDL "todavía no corrió", es que el proceso murió intentándolo. Un
   `SHOW COLUMNS` vacío en ese momento no distingue las dos cosas por sí
   solo. Antes de repetir el `SHOW COLUMNS`/`SHOW INDEX` de arriba:

   ```bash
   sudo systemctl restart jax-las-manos
   systemctl is-active jax-las-manos
   ```

   - Si da `active`: seguir con el `SHOW COLUMNS`/`SHOW INDEX` de arriba,
     como documentado.
   - Si NO da `active` (`failed`, `activating` en loop, etc.): buscar
     `init_tables:` en el log del servicio --

     ```bash
     sudo journalctl -u jax-las-manos -n 100 --no-pager | grep 'init_tables:'
     ```

     Un mensaje del tipo `init_tables: no se pudo agregar
     jacobs_pipelines.<columna> -- otra transacción tiene la tabla y venció
     la espera de 30 s (1205 ...)` confirma el freno: otra transacción
     tenía `jacobs_pipelines` tomada cuando el proceso arrancó. **El DDL es
     idempotente** (`init_tables()` vuelve a chequear `information_schema`
     antes de cada `ADD COLUMN`/`CREATE INDEX`, y una columna que ya existe
     no se reintenta) -- esperar a que la transacción que tiene la tabla
     termine (o encontrarla y matarla si quedó colgada,
     `SHOW PROCESSLIST`/`SHOW ENGINE INNODB STATUS`) y volver a
     `sudo systemctl restart jax-las-manos`. No hay nada que reparar a
     mano: es la misma corrida, repetida.
   - **No todo lo que crea `init_tables()` falla igual.** Las columnas
     CONTRATO -- `status_previo`, `descartado_por`, `descartado_at` y
     `visible` (Task 2 las escribe en la transición de estado; `visible` es
     generada pero jax-platform depende de que exista para filtrar su
     listado) -- fallan cerrado: si no se pueden crear, el proceso no
     arranca, porque un `UPDATE`/`SELECT` contra una columna que no existe
     rompería en producción de un modo peor (un `Unknown column` a mitad de
     una transición, no un arranque que se detiene limpio). Los **índices**
     (`idx_pipelines_descartados`, `idx_pipelines_ocultos`,
     `idx_pipelines_visibles` y los que ya traía Task 3) son fail-**soft**:
     si su propia espera de 30 s vence, queda un `ERROR` en el log con el
     nombre del índice y el arranque **sigue** -- el próximo reinicio los
     reintenta, y mientras tanto el plan de consulta es peor (o, para
     `idx_pipelines_visibles`, el `FORCE INDEX` explícito del SQL de
     jax-platform directamente da el error 1176 -- ver la nota del inicio
     de esta sección), no un servicio caído.

2. **Backend de jax-platform**, recién CON lo anterior confirmado. Antes de
   este punto, cualquier build de jax-platform que ya incluya este código
   (aunque sea una versión previa desplegada) seguiría sirviendo con el SQL
   viejo -- el riesgo es desplegar la VERSIÓN NUEVA del backend (con
   `FORCE INDEX (idx_pipelines_visibles)`) ANTES que el paso 1. Verificar
   por comportamiento, no por `is-active`:

   ```bash
   curl -s http://127.0.0.1:8080/api/pipelines -H "Authorization: Bearer <token>"
   # 200 con datos = ok. 500 = el paso 1 no terminó de verdad -- revisar
   # SHOW INDEX antes de reintentar, no reiniciar a ciegas.
   ```

3. **Frontend** (interno primero, después el sitio público -- pasos 3 y 4
   de arriba, sin cambios).

**Qué se rompe si se salta el orden** (corregido, fix round 5, 2026-09-22:
la versión anterior de este párrafo decía que TODOS estos endpoints
fallaban "en el proxy, antes de la consulta" -- falso; cada uno rompe en un
punto distinto, y en dos casos ni siquiera llega a pedirle nada a Jacobs).
Dos errores de MariaDB en juego, nombrados donde corresponden:

- **1054** `Unknown column '<col>' in '<clause>'`: la consulta SQL nombra
  una columna que no existe en la tabla.
- **1176** `Key '<índice>' doesn't exist in table '<tabla>'`: un
  `FORCE INDEX`/`IGNORE INDEX` nombra un índice que no existe.

Los dos llegan a `jax-platform` como una excepción de `aiomysql` sin
capturar -- el handler genérico de FastAPI los convierte en **500**, no en
un código propio del contrato de la API.

**Escenario que describe la tabla de abajo (cierre, Ruling 23, punto (g)):
NI jax#257 (descartar-pipelines: `status_previo`/`descartado_por`/
`descartado_at` + `idx_pipelines_descartados`/`idx_pipelines_ocultos`) NI
jax#259 (la columna `visible`/`idx_pipelines_visibles`) están desplegados
todavía** -- el backend nuevo de jax-platform saltó el paso 1 completo, no
sólo la mitad. Es el peor caso, y el que hay que evitar con el orden de
arriba.

| Qué | Dónde rompe primero | Por qué |
|---|---|---|
| `GET /api/pipelines` (lista principal) | **Local, SQL directo** | `FORCE INDEX (idx_pipelines_visibles)` -- **1176**, el índice no existe |
| `GET /api/pipelines?estado=discarded` ("Descartados") | **Local, SQL directo** | `SQL_DESCARTADOS_DEL_USUARIO` selecciona `descartado_at` -- **1054**, la columna no existe. NO llega a pedirle nada a Jacobs: revienta antes del proxy |
| `POST /pipelines/{id}/recover`, usuario NO superadmin | **Local, SQL directo** | `_estado_de_descarte()` (`api/pipelines.py`) hace `SELECT status, descartado_por ...` ANTES de decidir si deja pasar el pedido -- **1054**, `descartado_por` no existe. Tampoco llega al proxy |
| `POST /pipelines/{id}/discard`, `/hide`, `/restore` (y `/recover` de un superadmin) | **En Jacobs, no local** | Ninguno de estos consulta una columna nueva antes de proxear -- `_require_pipeline_owner`/`_require_pipeline_exists` sólo tocan columnas que YA existían. SÍ llegan al proxy; lo que devuelvan depende de qué tan vieja sea la versión de Jacobs contra la que pegan (sin las rutas de Task 3, un 404 de ruta inexistente -- no un 500 de columna) |

Sólo la fila de `discard`/`hide`/`restore` "llega al proxy" -- las otras
tres rompen ANTES, del lado de jax-platform, sin que Jacobs se entere del
pedido.

**Caso intermedio -- jax#257 desplegado, jax#259 todavía NO (cierre,
Ruling 23, punto (g)):** un despliegue a medias del paso 1, no todo o
nada. Acá `status_previo`/`descartado_por`/`descartado_at` y los índices
de jax#257 YA EXISTEN -- sólo falta `visible`/`idx_pipelines_visibles` de
jax#259. Eso cambia el cuadro de arriba:

| Qué | Con sólo jax#257 (sin jax#259) |
|---|---|
| `GET /api/pipelines` (lista principal) | Sigue en **1176/500** -- `FORCE INDEX (idx_pipelines_visibles)` sigue nombrando un índice que no existe. Es la ÚNICA fila que se queda igual de rota que en el peor caso |
| `GET /api/pipelines?estado=discarded` ("Descartados") | **Funciona** -- `descartado_at` ya existe con jax#257 solo |
| `POST /pipelines/{id}/recover`, usuario NO superadmin | **Funciona** -- `descartado_por` ya existe con jax#257 solo |
| `POST /pipelines/{id}/discard`, `/hide`, `/restore` (y `/recover` de un superadmin) | **Funciona** igual que en el peor caso -- no dependía de ninguna columna nueva |

O sea: con jax#257 desplegado pero no jax#259, el ÚNICO síntoma visible es
la lista principal en 500 -- todo lo demás (Descartados, recover/discard/
hide/restore) responde con normalidad, lo que puede confundir a quien
diagnostique "a medias funciona, no puede ser el paso 1" -- SÍ es el paso
1, sólo que incompleto. El chequeo de la sección 1 de arriba
(`SHOW COLUMNS ... LIKE 'visible'` / `SHOW INDEX ... idx_pipelines_visibles`)
es el que distingue este caso del peor caso: si `visible` ya aparece pero
`GET /api/pipelines` sigue en 500, revisar el índice aparte -- la columna
generada puede existir sin que el índice haya terminado de crearse (ver
`_agregar_columna_acotada`/reintento en `jax/jacobs/store.py`).

**Volver atrás, caso especial:** si este deploy salió en el orden
incorrecto y `GET /api/pipelines` está en 500, la reversión MÁS RÁPIDA es
la del backend de jax-platform (paso 2 de la sección "Volver atrás", más
abajo) al SHA anterior a este cambio -- no hace falta tocar `jax` ni el
esquema, que es aditivo.

## Caso general: un cambio toca el nombre del candado o el ejecutor programado

*(2026-09-27, Hyde. Ejecutado así el mismo día al desplegar la programación
configurable del catálogo, jax-platform#167.)*

**Por qué hace falta.** El sync del catálogo lo corren DOS caminos: el
`jax-catalogo-modelos.service` (oneshot, disparado por el timer) y el botón
«Sincronizar», que corre DENTRO de `jax-platform`. Los dos se excluyen con un
candado con nombre de MariaDB (`GET_LOCK`). Si un cambio **renombra** ese
candado (#167: `jax_catalogo_sync` → `jax_catalogo_sync:<base>`), el código
viejo y el nuevo dejan de verse: durante el despliegue podría haber **dos syncs
a la vez**. Este procedimiento cierra esa ventana.

> **Se ejecuta a mano, un paso por vez, mirando la salida.** No es un guion
> para pegar de corrido. Se intentó hacerlo guion desatendido y siete rondas
> de auditoría adversarial encontraron fallos nuevos en cada versión (un
> `exit` dentro de `sudo bash -c` que no frenaba el reinicio, un `merge` a
> `origin/master` antes de comparar el SHA, una reversión que volvía a
> avanzar). Para algo que pasa rara vez, un operador que mira cada salida es
> más seguro. **Si un paso no da exactamente lo esperado: no sigas, y ve a
> «Si hay que abortar».**

**Antes de empezar:** sección 0 (respaldo **con restauración probada**). Avisa
de que nadie use la pantalla de modelos durante el despliegue: entre el paso 3
y el 5 un clic en «Sincronizar» todavía podría colarse.

1. **Parar el timer.**
   ```bash
   sudo systemctl stop jax-catalogo-modelos.timer
   systemctl is-active jax-catalogo-modelos.timer     # tiene que dar: inactive
   ```

2. **Esperar a que no haya un sync programado corriendo ni encolado.** El
   servicio es `oneshot`: mientras corre, systemd lo da como `activating`, no
   `active` (por eso **no** sirve `is-active`).
   ```bash
   systemctl show -p ActiveState --value jax-catalogo-modelos.service   # tiene que dar: inactive (o failed)
   systemctl list-jobs --no-legend jax-catalogo-modelos.service         # tiene que dar: nada
   ```
   Si da `activating`, esperar y repetir (una corrida dura segundos; el tope
   del servicio es 30 min).

3. **Freno: ningún sync con NINGUNO de los dos nombres de candado.** Cubre el
   timer y el botón, el código viejo y el nuevo, y funciona aunque la tabla
   `catalogo_sync_ejecucion` todavía no exista (primer despliegue). Las
   credenciales van en un archivo de opciones `600` efímero, nunca en argv
   (`/proc` no tiene `hidepid`):
   ```bash
   sudo bash -c '
   set -euo pipefail; set -a; . /etc/jax/.env; set +a
   T=$(mktemp -d); chmod 700 $T; trap "rm -rf $T" EXIT
   printf "[client]\nhost=%s\nport=%s\nuser=%s\npassword=%s\ndatabase=%s\n" \
     "$JAX_DB_HOST" "$JAX_DB_PORT" "$JAX_DB_USER" "$JAX_DB_PASSWORD" "$JAX_DB_NAME" > $T/c.cnf
   chmod 600 $T/c.cnf
   mariadb --defaults-extra-file=$T/c.cnf -N -e \
     "SELECT IS_USED_LOCK('"'"'jax_catalogo_sync'"'"'), IS_USED_LOCK(CONCAT('"'"'jax_catalogo_sync:'"'"', DATABASE()))"'
   ```
   **Tiene que dar `NULL	NULL`.** Un número es el id de la conexión que
   sincroniza: esperar y repetir.

4. **Actualizar el checkout al SHA exacto** (40 caracteres, el merge del PR;
   nunca `origin/master` a secas, que puede haber avanzado):
   ```bash
   SHA=<sha-de-40-caracteres>
   cd /srv/jax-prod/jax-platform && git fetch origin \
     && git merge-base --is-ancestor HEAD "$SHA" && git merge --ff-only "$SHA"
   git rev-parse HEAD            # tiene que dar exactamente $SHA
   git status -s                 # tiene que dar: nada (el ExecStartPre exige árbol limpio)
   ```

5. **Reiniciar el backend y verificar.**
   ```bash
   sudo systemctl restart jax-platform
   curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://127.0.0.1:8080/api/health   # 200
   ```

6. **Reinstalar las TRES unidades**, verificando cada archivo del repo **antes**
   de instalarlo:
   ```bash
   U=/srv/jax-prod/jax-platform/ops/migration/systemd-units
   for f in jax-catalogo-modelos.service jax-catalogo-modelos.timer jax-catalogo-modelos-aviso.service; do
     sudo systemd-analyze verify "$U/$f" && sudo install -m 644 -o root -g root "$U/$f" /etc/systemd/system/$f
   done
   sudo systemctl daemon-reload
   ```
   `verify` no tiene que mencionar ninguna de las tres (los avisos de otras
   unidades del sistema no cuentan).

7. **Arrancar el timer.**
   ```bash
   sudo systemctl start jax-catalogo-modelos.timer
   systemctl list-timers jax-catalogo-modelos.timer --no-pager
   ```

8. **Seguir con las secciones 3, 4 y 5** (frontend interno, sitio público,
   verificación desde afuera) si el cambio toca el frontend.

9. **Verificación funcional:** pulsar «Sincronizar» como superadmin. Mientras
   corre, el paso 3 tiene que dar `NULL	<id>` (el candado **nuevo** tomado);
   al terminar, la última fila de `catalogo_sync_ejecucion` tiene que cerrar
   `ok` o `con_problemas`, nunca `error`.

**Si hay que abortar** (antes del paso 5): nada se reinició. Si ya se hizo el
paso 4, devolver el checkout a como estaba con
`git -C /srv/jax-prod/jax-platform reset --hard <SHA-previo>` (anotado en
`ESTADO-ANTES.txt` de la sección 0), comprobar `git status -s` vacío, y
**volver a arrancar el timer** (`sudo systemctl start jax-catalogo-modelos.timer`):
si no, el catálogo deja de sincronizarse sin que nadie avise.

**Volver atrás un despliegue ya hecho:** el mismo procedimiento, con dos
cambios. En el paso 4, `git reset --hard <SHA-previo>` en lugar del `merge`
(se hace **después** de los pasos 1-3, nunca antes, porque si no el timer
podría correr el código viejo con el candado viejo). En el paso 6, reinstalar
las unidades de ese SHA.

## Migraciones B9 adicionales (`jax/memory/b9_migrations/`, más allá de 001-003)

*(Agregado 2026-09-27, auditoría adversarial del PR #164, MAJOR-2.)* El esquema B9
vive en `jax_memory` (compartido) pero **`run_migrations()` de jax-platform sólo
aplica 001 (`001_b9_shared_memory.sql`), 002 (`002_b9_hardening.sql`) y 003
(`003_project_scope_authority.sql`, vía el hook Python de JAX)**. El propio README
de esa carpeta es explícito: la cadena entera "is not executed by application
import or worker startup" -- todo lo que quede después de 003 (004, 006, y lo que
JAX agregue mañana) es DDL que **nadie aplica solo, en ningún lado**. Aplicarlo es
un paso manual, y declarar que ya se aplicó es OTRO paso manual, separado.

**Cuándo hace falta este paso.** Cuando `jax` agrega un `.sql` nuevo a
`jax/memory/b9_migrations/` y el código que lo necesita (en `jax` o en
`jax-platform`) va a producción. La señal de que falta: la suite de jax-platform
revienta con

```
BaseDeTestInvalida: migración B9 <archivo> no declarada como aplicada en
producción: aplícala en producción siguiendo docs/runbooks/despliegue.md
(sección 'Migraciones B9 adicionales') y agregala a
b9_migraciones_en_produccion.json antes de que la suite la use.
```

(`backend/base_de_test.py::aplicar_migraciones_b9_restantes`, que descubre estos
archivos por glob contra el manifiesto de abajo -- NO los aplica a ciegas: que un
`.sql` exista en el repo de JAX no prueba que ya pasó por este flujo revisado.)

### Pasos

0. **GO explícito de Fernando, ANTES de tocar nada.** *(MINOR-B, ronda 2 de la
   auditoría del PR #164.)* Esto no es un `ALTER` aditivo de rutina: 004 hace
   `DROP PRIMARY KEY`/`DROP INDEX` sobre una tabla con filas reales, y 006 deja
   `memory_revisions.tenant_id` en `NOT NULL` con una FK nueva -- los dos son DDL
   que reescribe la forma de una tabla de PRODUCCIÓN con datos adentro, no un
   `ADD COLUMN NULL` que no le puede doler a nadie. Sin el GO, no se pasa al paso 1.
1. **Respaldo primero** (Principio VI, sección 0 de arriba) -- **y probarlo**,
   restaurando en una base descartable antes de seguir. Sin esto no hay paso 2.
2. **Leer el archivo entero antes de aplicarlo.** Los `.sql` de esa carpeta NO son
   idempotentes en general (verificado en 004 y 006, 2026-09-27: `ADD COLUMN` sin
   `IF NOT EXISTS`, `DROP PRIMARY KEY`/`DROP INDEX` que asumen que lo que borran
   sigue ahí, `CREATE TRIGGER`/`ADD CONSTRAINT` sin `IF NOT EXISTS`) -- correrlo dos
   veces contra la misma base revienta a mitad. Aplicarlo UNA vez, a mano, contra
   `jax_memory`, con el archivo EXACTO que se va a declarar en el paso 4 (mismo
   byte a byte -- es de ahí que sale el `sha256` de ese paso, no de una copia
   editada a mano ni de memoria):

   ```bash
   set -a; . <(sudo -n cat /etc/jax/.env); set +a
   ARCHIVO=/srv/jax-prod/jax/jax/memory/b9_migrations/<archivo>.sql
   # El hash se calcula ANTES de aplicar nada, sobre el archivo que se está
   # por correr -- ANOTALO (pegalo en el ticket/PR de este despliegue): es el
   # valor que el paso 4 va a exigir, byte a byte, antes de declarar la
   # migración. Calcularlo DESPUÉS (de memoria, o de una copia editada) es
   # exactamente el error que este control existe para atrapar.
   sha256sum "$ARCHIVO"
   mysql -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u"$JAX_DB_USER" -p"$JAX_DB_PASSWORD" \
     jax_memory < "$ARCHIVO"
   ```

   **Si falla a mitad (MariaDB corta la conexión, un error de sintaxis, un lock que
   vence): DETENERSE. No reintentar el archivo entero.** MariaDB hace commit
   implícito por sentencia DDL -- no hay rollback que deshaga lo que ya corrió, y
   reintentar desde el principio puede chocar con lo que sí quedó aplicado (mismo
   defecto, en la base de tests, que documenta MINOR-3 de esta auditoría en
   `base_de_test.py`). Dos salidas, ninguna a ciegas:
   - **Restaurar desde el respaldo del paso 1** (la más segura: vuelve la tabla al
     estado de antes de este cambio) y volver a empezar desde el paso 0.
   - **Completar sentencia por sentencia, con Fernando delante**, leyendo el
     `.sql` y ejecutando cada sentencia que falta a mano, verificando el estado de
     la tabla entre una y otra (`SHOW CREATE TABLE`) -- sólo si restaurar el
     respaldo no es viable por el tiempo que ya pasó con datos nuevos escritos.
3. **Verificar en la base, no suponer.** El `SHOW COLUMNS`/`SHOW INDEX`/consulta a
   `information_schema` que confirme la forma final que describe el propio
   `.sql` -- exactamente lo que va a quedar escrito en el manifiesto del paso 4.
4. **Declarar la migración en `jax-platform`**, en el MISMO cambio que cualquier
   código que dependa de ella (o antes, si nada la usa todavía):
   `backend/b9_migraciones_en_produccion.json` gana una clave nueva con el nombre
   exacto del archivo, la fecha, quién la aplicó, CÓMO se verificó (la consulta
   del paso 3, no "se ve bien") y el `sha256` **del archivo exacto que se aplicó
   en el paso 2**. **Antes de pegar el hash en el manifiesto, recalculalo y
   compará contra el que anotaste en el paso 2:**

   ```bash
   sha256sum "$ARCHIVO"   # tiene que dar LITERALMENTE lo mismo que anotaste en el paso 2
   ```

   Si no coincide -- el archivo cambió entre el paso 2 y ahora, o el que se
   copió al manifiesto no es el que se aplicó -- NO se declara: hay que volver
   al paso 2 con el archivo correcto, nunca "arreglar" el hash a mano para que
   cierre. Sin esta clave, la suite de tests revienta con el mensaje de arriba;
   con la clave pero el hash equivocado, revienta igual, con un mensaje que dice
   "el contenido cambió" -- es la baranda, no un trámite.
5. **Correr la suite de jax-platform** (`aplicar_migraciones_b9_restantes` recoge
   el archivo nuevo por glob, lo ve declarado en el manifiesto con el hash que
   coincide, y lo aplica en la base de tests) para confirmar que el código que la
   necesita pasa contra el esquema real.

**Volver atrás.** *(Acotado, MINOR-B ronda 2: la versión anterior de este párrafo
decía "el esquema B9 es aditivo... revertir el código no exige revertir el DDL" --
cierto para 001/002/003, FALSO en general para lo que este runbook cubre.)* 004 y
006 NO son aditivos puros: 004 hace `DROP PRIMARY KEY`/`DROP INDEX` sobre
`memory_legacy_bindings`/`memory_objects`, y 006 deja `memory_revisions.tenant_id`
`NOT NULL` con una FK -- revertir el CÓDIGO que los usa no repone la forma vieja de
esas tablas, y con filas ya escritas bajo el esquema nuevo, revertir el DDL mismo
puede perder datos o violar la FK. Si hiciera falta revertir el DDL, es un caso a
mano, con Fernando, con el respaldo del paso 1 como red -- nunca una decisión
unilateral de la sesión que despliega.

## Proyectos y documentos (E2a): permisos de `proyectos/` ANTES de la primera subida

*(2026-10-03, jax-platform#186.)* `proyectos/` -- la carpeta `proyectos/` dentro de `JAX_WORKSPACE_DIR`
-- tiene que tener **setgid y el grupo `fruiz`**, aplicados con el guion de **jax**
`ops/permisos_proyectos.py --aplicar`, **antes de la primera subida de documentos**. Hay que hacerlo en
todo servidor nuevo o con un workspace nuevo (y la verificación, `--verificar`, después de cada despliegue
que toque el almacén).

    # desde el checkout de jax (el que tiene ops/permisos_proyectos.py; usa `sudo -n` y lee JAX_WORKSPACE_DIR
    # de /etc/jax/.env; la regla de sudoers que lo permite NO la fija el repo)
    python3 ops/permisos_proyectos.py --aplicar
    python3 ops/permisos_proyectos.py --verificar          # debe dar rc=0

**Por qué.** `almacen.abrir_carpeta_lote` ya no hace `chmod` a las carpetas (un chmod de `jaxsvc`, que no es del
grupo `fruiz`, les borra el setgid): las crea con `mkdir` y hereda grupo, setgid y ACL del padre. Y exige, en
**cada** nivel que abre o crea bajo `proyectos/` (incluido `proyectos/` mismo, que es el ancla), setgid puesto y
el grupo de `proyectos/`.

**Si falta, qué se ve.** Toda subida falla cerrada: `500 almacen_herencia_rota` (el detalle va al log del
backend, "una carpeta del workspace no heredo los permisos del padre"), sin escribir nada ni dejar filas.
Lo mismo si `proyectos/` no existe (se crea sin setgid) o si algún `proyectos/<uuid>/` ya existía sin setgid. No se
arregla con un `chmod` a mano en la carpeta del proyecto: se corre el guion, que deja todo el árbol coherente.
`reprocesar` no pasa por aquí: solo lee `fuente/` y borra, con `unlink`, copias de `entrada/`.

## Volver atrás

1. **Sitio público:** copiar de vuelta `~/respaldos-sitio/axioma-<fecha>/` en
   atem-ai. Es lo más rápido y lo más visible.
2. **Código:** `git -C <checkout> reset --hard <SHA de ESTADO-ANTES.txt>` y
   reiniciar la unidad.
3. **Esquema:** las migraciones de esta casa son **aditivas** (columnas nullable,
   índices). Volver el código NO exige volver el esquema, y no hay que borrar
   columnas para revertir. Si hiciera falta restaurar datos, está el volcado —
   pero eso pierde lo escrito desde el respaldo, así que es el último recurso y
   se decide con Fernando.
