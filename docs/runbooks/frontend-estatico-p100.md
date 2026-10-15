# P100 · frontend interno compilado en nginx

**Decisión · Hyde, 2026-10-14 20:42 CST, con delegación de Fernando (OP#270).** Este procedimiento sustituye el PR #213 rechazado. El servicio sigue siendo `jax-platform-frontend.service` en `:5173`, con el usuario efectivo de sus drop-ins existentes. El sitio público de atem-ai se despliega aparte. **No ejecutar la activación antes del APROBADO de Hyde.**

**Hecho · Codex, 2026-10-14 21:07 CST.** Dos builds limpios del commit de partida `master` dieron manifiestos idénticos; la prueba systemd de una unidad desechable en `/run` pasó aplicar, reiniciar nginx, detener, cambiar marcadores de boot/checkout, revertir, matar nginx y revertir desde fallo. El servicio productivo no se cambió. Los marcadores sólo simulan el cambio de identidad: la reversión no lee PID, boot, checkout ni SHA. La medición de carga productiva y las verificaciones visuales siguen pendientes del GO de despliegue.

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

Antes del cambio, guardar `systemctl cat jax-platform-frontend.service` y el resultado de la carga Vite. El drop-in nuevo limpia `ExecStartPre` y `ExecStart` de Vite, fija `WorkingDirectory=/`, lanza el nginx de la distro con `daemon off` y mantiene `User`/`Group` heredados. El nginx usa `RuntimeDirectory` de systemd, sirve SPA, da caché inmutable sólo a `/assets/`, evita caché para `index.html`, y conserva `/api` y `/ws` hacia `localhost:8080` como `vite.config.js`.

## Activar y comprobar

```bash
sudo python3 "$ROOT/ops/frontend/switch.py" apply --release "/opt/jax-frontend-releases/$SHA"
systemctl status jax-platform-frontend.service --no-pager
curl -fsS http://127.0.0.1:5173/ | cmp - "/opt/jax-frontend-releases/$SHA/dist/index.html"
curl -fsSI http://127.0.0.1:5173/index.html
curl -fsSI http://127.0.0.1:5173/assets/$(find "/opt/jax-frontend-releases/$SHA/dist/assets" -maxdepth 1 -name 'index-*.js' -printf '%f\n' | head -1)
```

`apply` ejecuta `nginx -t` antes de instalar el drop-in, después `systemctl daemon-reload`, `reset-failed`, `restart` y `curl`; compara los bytes de `index.html` con el release elegido. Si falla tras instalar, intenta la reversión en el mismo proceso y declara cualquier fallo de recuperación. El operador verifica además `/api/health`, login, una pantalla con datos, ES/EN y claro/oscuro. Se repite la misma carga antes/después y se anotan RPS, p95, concurrencia y errores. Si el resultado es neutro o peor, se revierte. No inferir que el sitio público cambió: ese despliegue tiene su propio runbook.

## Emergencia: volver a Vite (5 líneas)

```bash
ROOT=/srv/jax-prod/jax-platform
sudo python3 "$ROOT/ops/frontend/switch.py" rollback
systemctl status jax-platform-frontend.service --no-pager
curl -fsS http://127.0.0.1:5173/ >/dev/null
systemctl cat jax-platform-frontend.service
```

`rollback` borra sólo `zz-static.conf` y `static-nginx.nginx`, recarga systemd, intenta `reset-failed` y reinicia la unidad. Funciona aunque nginx haya muerto, cambie su PID, se reinicie el host o avance el checkout Git. Si no arranca Vite, revisar `journalctl -u jax-platform-frontend.service -n 100 --no-pager` y la integridad del checkout/base; el script no declara éxito cuando `restart` falla. El release en `/opt` queda para diagnóstico, sin proceso que lo sirva.

**Pendiente · Hyde/Fernando, tras auditoría:** autorizar la activación, medir Vite antes y nginx después bajo el mismo guion, verificar la app en navegador y registrar números y decisión en la Biblioteca. No se modificó `.github/workflows/policy.yml` porque es archivo reservado. Hace falta un job CI que corra `python3 -m unittest discover -s ops/frontend/tests -v` con nginx y systemd reales para que la prueba integrada no quede sólo local; Fernando decide ese cambio de workflow por separado.

**Lección técnica · Codex, 2026-10-14.** Un archivo `*.conf` dentro de `unidad.service.d/` lo interpreta systemd como drop-in, aunque su contenido sea de nginx; el archivo generado usa extensión `.nginx`. En una unidad systemd de usuario, nginx tampoco pudo abrir `/dev/stdout` como access log; el nuevo config lo desactiva. Se descartó restaurar con CAS de PID/boot/SHA porque impedía la reversión precisamente tras las fallas que debe cubrir (veredicto de Hyde sobre #213).
