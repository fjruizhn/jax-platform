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
