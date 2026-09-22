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

```bash
set -a; . <(sudo -n cat /etc/jax/.env); set +a
D=~/respaldos-despliegue/$(date +%Y-%m-%d)-<motivo>; mkdir -p $D

# Los SHA a los que volver, ANTES de tocar nada
{ echo "fecha: $(date -Is)"
  echo "jax prod:          $(git -C /srv/jax-prod/jax rev-parse HEAD)"
  echo "jax-platform prod: $(git -C /srv/jax-prod/jax-platform rev-parse HEAD)"
  echo "bundle publico:    $(curl -s https://axioma-ia.io | grep -oE 'assets/index-[^"]*\.js' | head -1)"
} | tee $D/ESTADO-ANTES.txt

mariadb-dump -h "$JAX_DB_HOST" -P "$JAX_DB_PORT" -u "$JAX_DB_USER" \
  -p"$JAX_DB_PASSWORD" --single-transaction jax_memory | gzip > $D/jax_memory.sql.gz
```

**Y restauralo en una base descartable antes de seguir** — un respaldo sin
restauración probada no es un respaldo:

```bash
B=jax_memory_test_restauracion
mariadb ... -e "DROP DATABASE IF EXISTS \`$B\`; CREATE DATABASE \`$B\`;"
gunzip -c $D/jax_memory.sql.gz | sed -e '/^CREATE DATABASE/d' \
  -e "s/^USE \`jax_memory\`;/USE \`$B\`;/" | mariadb ... "$B"
# comparar COUNT(*) de facts contra produccion, y DROP la base de prueba
```

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
