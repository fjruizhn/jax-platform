# P100 · frontend interno compilado en nginx

**Decisión · Hyde, 2026-10-14 20:42 CST, con delegación de Fernando (OP#270).** Este procedimiento sustituye el PR #213 rechazado. El servicio sigue siendo `jax-platform-frontend.service` en `:5173`, con el usuario efectivo de sus drop-ins existentes. El sitio público de atem-ai se despliega aparte. **No ejecutar la activación antes del APROBADO de Hyde.**

**Hecho · Codex, 2026-10-14 21:07 CST.** Dos builds limpios del commit de partida `master` dieron manifiestos idénticos; la prueba systemd de una unidad desechable en `/run` pasó aplicar, reiniciar nginx, detener, revertir, matar nginx y revertir desde fallo. El servicio productivo no se cambió. La reversión no lee PID, boot, checkout ni SHA. La medición de carga productiva y las verificaciones visuales siguen pendientes del GO de despliegue.

**Auditoría Hyde, 2026-10-14 21:42 CST · APROBADO CON CAMBIOS.** El checkout productivo pertenece a `jaxsvc` y tiene modo 775. Un script ejecutado como root desde allí daría escalada a un `jaxsvc` comprometido; la copia ejecutable de `switch.py` se instala root:root desde el SHA auditado y se coteja después. La emergencia usa sólo shell y systemd, sin repo. El proxy `/api` espera 600 s en ambos sentidos: Ollama puede tardar 180 s una vez tomado el carril y otra petición puede estar delante. Los 420 s adicionales dan margen de cola y procesamiento; el carril no tiene tope absoluto de espera, por lo que ninguna cota finita garantiza todas las colas. Medirlas en la prueba de carga y revisar ese límite si se agota.

## Preparar el release tras integrar el PR

Ejecutar como operador en Hall9000. Estos comandos usan el checkout productivo **ya actualizado al commit aprobado**; el SHA se obtiene en vivo. `npm` y `node` deben estar en `PATH` del operador. `build.py` extrae exactamente el árbol Git de ese SHA y ejecuta `npm ci --include=dev && npm run build` fuera del checkout. Rechaza variables `VITE_*` externas no declaradas; los `.env` versionados, si existen, quedan cubiertos por el SHA de fuente. El manifiesto guarda SHA de fuente, SHA-256 del lockfile, versiones Node/npm y SHA-256 de cada archivo. No reescribe un release existente.

```bash
ROOT=/srv/jax-prod/jax-platform
SHA=$(git -C "$ROOT" -c safe.directory="$ROOT" rev-parse HEAD)
BUILD_DIR=$(mktemp -d)
python3 "$ROOT/ops/frontend/build.py" --repo "$ROOT" --releases "$BUILD_DIR" --revision "$SHA"
sudo install -d -o root -g root -m 0755 /opt/jax-frontend-releases
sudo test ! -e "/opt/jax-frontend-releases/$SHA"
sudo test ! -e "/opt/jax-frontend-releases/.$SHA.new"
sudo cp -a "$BUILD_DIR/$SHA" "/opt/jax-frontend-releases/.$SHA.new"
sudo chown -R root:root "/opt/jax-frontend-releases/.$SHA.new"
sudo mv "/opt/jax-frontend-releases/.$SHA.new" "/opt/jax-frontend-releases/$SHA"
sudo -u jaxsvc test -r "/opt/jax-frontend-releases/$SHA/dist/index.html"
rm -r -- "$BUILD_DIR"
```

Instalar la herramienta **desde el SHA exacto que Hyde auditó**, no desde un archivo del checkout vivo. `SWITCH_SHA` es ese SHA, copiado del veredicto; un avance posterior de `HEAD` no lo cambia. `git show` sólo lee el objeto Git; root ejecuta exclusivamente la copia bajo `/usr/local/libexec`. El segundo `git show` compara sus bytes con los instalados, y `stat` verifica dueño y modo. Si cualquiera falla, no activar.

```bash
set -euo pipefail
ROOT=/srv/jax-prod/jax-platform
SWITCH_SHA='PEGAR_SHA_AUDITADO'
SWITCH=/usr/local/libexec/jax-frontend-switch.py
SWITCH_TMP=$(mktemp)
git -C "$ROOT" show "$SWITCH_SHA:ops/frontend/switch.py" > "$SWITCH_TMP"
sudo install -d -o root -g root -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0755 "$SWITCH_TMP" "$SWITCH"
git -C "$ROOT" show "$SWITCH_SHA:ops/frontend/switch.py" | sudo cmp - "$SWITCH"
sudo test "$(stat -c '%U:%G:%a' "$SWITCH")" = root:root:755
rm -- "$SWITCH_TMP"
```

Antes del cambio, guardar `systemctl cat jax-platform-frontend.service` y el resultado de la carga Vite. El drop-in nuevo limpia `ExecStartPre` y `ExecStart` de Vite, fija `WorkingDirectory=/`, lanza el nginx de la distro con `daemon off` y mantiene `User`/`Group` heredados. El nginx usa `RuntimeDirectory` de systemd, sirve SPA, da caché inmutable sólo a `/assets/`, evita caché para `index.html`, y conserva `/api` y `/ws` hacia `localhost:8080` como `vite.config.js`.

## Activar y comprobar

```bash
sudo python3 "$SWITCH" apply --release "/opt/jax-frontend-releases/$SHA"
systemctl status jax-platform-frontend.service --no-pager
curl -fsS http://127.0.0.1:5173/ | cmp - "/opt/jax-frontend-releases/$SHA/dist/index.html"
curl -fsSI http://127.0.0.1:5173/index.html
curl -fsSI http://127.0.0.1:5173/assets/$(find "/opt/jax-frontend-releases/$SHA/dist/assets" -maxdepth 1 -name 'index-*.js' -printf '%f\n' | head -1)
```

`apply` ejecuta `nginx -t` antes de instalar el drop-in, después `systemctl daemon-reload`, `reset-failed`, `restart` y `curl`; compara los bytes de `index.html` con el release elegido. Si falla tras instalar, intenta la reversión en el mismo proceso y declara cualquier fallo de recuperación. Una prueba sin root fuerza una respuesta `curl` de bytes distintos y confirma que retira ambos archivos y reinicia Vite. El operador verifica además `/api/health`, login, una pantalla con datos, ES/EN y claro/oscuro. Se repite la misma carga antes/después y se anotan RPS, p95, concurrencia y errores. Si el resultado es neutro o peor, se revierte. No inferir que el sitio público cambió: ese despliegue tiene su propio runbook. Vite admite `localhost` y hosts IP literales; nginx permite los nombres públicos, `localhost` y `127.0.0.1`, y responde 444 a otras IP literales. El acceso soportado desde otra máquina usa el FQDN.

## Emergencia: volver a Vite (5 líneas)

```bash
sudo rm -f /etc/systemd/system/jax-platform-frontend.service.d/zz-static.conf /etc/systemd/system/jax-platform-frontend.service.d/static-nginx.nginx
sudo systemctl daemon-reload
sudo systemctl reset-failed jax-platform-frontend.service || true
sudo systemctl restart jax-platform-frontend.service
curl -fsS http://127.0.0.1:5173/ >/dev/null
```

La emergencia borra sólo `zz-static.conf` y `static-nginx.nginx`, recarga systemd, intenta `reset-failed` y reinicia la unidad. Funciona aunque nginx haya muerto, cambie su PID, se reinicie el host o el checkout Git se mueva o falte. Para una reversión normal también sirve `sudo python3 "$SWITCH" rollback` desde la copia root:root; nunca ejecutar el script del checkout como root. Si no arranca Vite, revisar `journalctl -u jax-platform-frontend.service -n 100 --no-pager` y la integridad del checkout/base; no declarar éxito cuando `restart` falla. El release en `/opt` queda para diagnóstico, sin proceso que lo sirva.

**Pendiente · Hyde/Fernando, tras auditoría:** autorizar la activación, medir Vite antes y nginx después bajo el mismo guion, verificar la app en navegador y registrar números y decisión en la Biblioteca. No se modificó `.github/workflows/policy.yml` porque es archivo reservado. La prueba integrada de systemd salta sin root, nginx y systemctl; para ejecutarla en CI hace falta un job con esas condiciones que corra `python3 -m unittest discover -s ops/frontend/tests -v` sin skips. Fernando decide ese cambio de workflow por separado.

**Lección técnica · Codex, 2026-10-14.** Un archivo `*.conf` dentro de `unidad.service.d/` lo interpreta systemd como drop-in, aunque su contenido sea de nginx; el archivo generado usa extensión `.nginx`. En una unidad systemd de usuario, nginx tampoco pudo abrir `/dev/stdout` como access log; el nuevo config lo desactiva. Se descartó restaurar con CAS de PID/boot/SHA porque impedía la reversión precisamente tras las fallas que debe cubrir (veredicto de Hyde sobre #213).
