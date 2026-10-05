# Contribuir a Axioma Platform

Mismas reglas de fondo que el repo hermano `jax` -- éstas son las que ya
se aplican al propio trabajo del mantenedor, no un estándar nuevo para
terceros.

## Reglas de la casa

**No suponer, verificar contra el sistema real.** Un PR que dice "esto
debería andar" sin evidencia de que corrió contra el backend/frontend
real no está terminado. Esto incluye UI: si tocás un componente, probalo
en el navegador, no solo en el linter.

**i18n, cero excepciones.** Ningún string visible al usuario va
hardcodeado en un componente -- todo vive en `frontend/src/i18n/{es,en}.js`.
Un PR con un string nuevo sin su entrada de traducción no se acepta.

**Dark/light mode, siempre.** Todo componente nuevo respeta el tema
activo -- colores vía variables CSS/tokens, nunca un valor hardcodeado.
Probado en ambos modos antes de pedir review.

**P10 -- ningún camino de error termina en éxito reportado.** El repo
`jax` corre un scanner de CI que busca `except: pass` sin marcar; el mismo
criterio aplica acá aunque el scanner viva en el otro repo. Un `except`
que silencia un error real (sin loguear, sin propagar) no se acepta salvo
que esté marcado `# fail-soft: <razón concreta>`.

**Tests obligatorios.** Backend: contra una base de datos de prueba real
cuando el comportamiento depende de la integración, no todo mockeado.
Frontend: al menos verificación manual documentada en el PR (qué se probó,
en qué navegador/modo) si no hay test automatizado para ese componente.

## Correr la suite del backend fuera de CI

`backend/tests/conftest.py` carga de `/etc/jax/.env` (con `sudo -n cat`) el host, el
puerto y las credenciales de la base, salvo bajo `JAX_CI_NO_DB=1`. En una máquina que
tiene ese archivo -- hall9000 -- eso apunta la suite a la MariaDB de **producción**
(3308), donde crea y borra bases `jax_memory_test_*`.

Desde #195 esa conexión se niega sola: fuera de CI (`CI` y `GITHUB_ACTIONS=true`), un
`JAX_DB_PORT` de 3306 o 3308 falla cerrado con el nombre de la variable que lo levanta.
Dos salidas:

1. **Contenedor desechable en otro puerto (lo recomendado).** Nada de producción se toca.

   ```bash
   docker run -d --rm --name jxp-test-db -e MARIADB_ROOT_PASSWORD=ci \
     -e MARIADB_DATABASE=jax_memory_test -p 127.0.0.1:33991:3306 mariadb:12.3.3
   # esquema de la plantilla, igual que el job con DB de CI (JAX = checkout del repo jax):
   sed -e '/^CREATE DATABASE IF NOT EXISTS jax_memory$/,/;$/d' \
       -e 's/^USE jax_memory;$/USE `jax_memory_test`;/' "$JAX/jax_memory_schema.sql" \
     | MYSQL_PWD=ci mariadb -h127.0.0.1 -P33991 -uroot jax_memory_test
   # (y, como en CI, `store.init_tables()` de jax sobre esa base)
   cd backend
   env -i PATH=/usr/bin:/bin HOME=/tmp/h JAX_JWT_SECRET=ci-dummy \
     JAX_REPO_PATH="$JAX" JAX_CONFIG_PATH="$JAX/config/config.toml" \
     JAX_WORKSPACE_DIR=/tmp/jax-ws PYTHONPATH="$JAX:$JAX/las_manos" \
     JAX_DB_HOST=127.0.0.1 JAX_DB_PORT=33991 JAX_DB_USER=root JAX_DB_PASSWORD=ci \
     JAX_DB_NAME=jax_memory_test .venv/bin/python -m pytest -q
   ```

   `env -i` deja el entorno vacío; el conftest hace `setdefault`, así que lo que ponés
   a mano gana. Para estar seguro de que **no** lee `/etc/jax/.env`, poné al frente del
   `PATH` un `sudo` falso que salga con error y `No such file` en stderr. Sin base:
   `JAX_CI_NO_DB=1` en lugar de las variables `JAX_DB_*` (salta lo que pide MariaDB y no
   lee el archivo).

2. **Contra la instancia de producción, a propósito.** Exportar
   `JAX_TEST_DB_PERMITIR_INSTANCIA_DE_PRODUCCION=1`. Solo habilita usar el puerto
   3306/3308 y la lectura de `jax_memory`; nunca escribir en una base sin sufijo de test.

## Qué esperamos de un PR

- Rama dedicada, commits que expliquen el *por qué*.
- CI verde.
- Si tocás el flujo de aprobación de cambio de modelo (`facet_binding`) o
  cualquier ruta que hoy exige superadmin, decilo explícito en la
  descripción -- es una de las pocas rutas del sistema con esa exigencia
  a propósito, y un PR que la relaje necesita esa conversación primero.

## Qué NO esperamos

No hace falta cobertura perfecta ni que el PR resuelva toda la deuda
relacionada que encuentres en el camino -- señalala en la descripción,
no la escondas ni la arregles a medias dentro de un PR que no era sobre
eso.
