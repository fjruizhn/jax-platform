# Tema claro/oscuro con tokens semánticos — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que todo el frontend de jax-platform pinte con tokens semánticos (variables CSS con valor claro y oscuro), que `theme_default` funcione sin parpadeo, que Inter se cargue de verdad y que un test exija WCAG AA en los dos temas.

**Architecture:** los valores viven sólo en `frontend/src/tema/tokens.css` (`:root` = oscuro, `:root[data-tema="claro"]` = claro); `tema/tokens.js` tiene los nombres, la matriz de pares y `colorToken()`; Tailwind mapea cada token a `rgb(var(--x) / <alpha-value>)`. Un script en línea en `index.html` pone `data-tema` antes del primer pintado, y un endpoint público `GET /api/apariencia` entrega el predeterminado. Cuatro PRs: base + Login + barra; admin; chat y pipelines; resto y cierre. Un último PR en `jax` actualiza DEUDA.

**Tech Stack:** React 19, Vite 6, Tailwind 3.4, zustand 5, vitest 4 (jsdom/node), FastAPI + aiomysql, MariaDB 12.3 (`jax_memory_test` para tests), `@fontsource/inter` 5.3.0.

**Spec:** `docs/superpowers/specs/2026-09-14-tema-tokens-design.md` (commit `f98fe9f`, aprobado por Fernando 2026-09-14). El plan argumenta desde el spec; quien ejecuta lee los dos.

## Respuestas del dueño a las preguntas abiertas del spec (2026-09-14, vinculantes)

1. **Facetas:** los colores de identidad que fallan AA como texto en oscuro (`jax_local`, `jekyll`, `ada`, DALL·E) suben un paso, al tono 400 de su misma familia. Sin tokens extra. (Es lo que ya trae la tabla §3.2 del spec.)
2. **Orden:** la etapa 2 de usuarios (`feat/admin-usuarios-etapa2-sesiones`, worktree `/home/fruiz/worktrees/jax-platform-etapa2`) se mergea **primero**. El PR 1 de tema se rebasa sobre ella (Task 1).
3. **CSP:** medido el 2026-09-14, `axioma-ia.io` no manda `Content-Security-Policy` (el vhost de aaPanel sólo agrega `Cache-Control`). El script en línea no se bloquea. El deploy lo vuelve a comprobar (Task 12).

## Global Constraints

- **Worktree:** todo se hace en `/home/fruiz/worktrees/jax-platform-tema`. **Nunca** se edita, testea ni commitea en `/home/fruiz/jax-platform` ni en `/home/fruiz/jax`: son los checkouts de producción. La única excepción es el deploy (pull `--ff-only` en master después del merge, con el GO de Fernando).
- **Git:** `git -C <ruta absoluta>` siempre; nada de `cd` compuesto del que dependa un commit. Los archivos se agregan uno por uno (`git add <ruta>`), **nunca** `git add -A` ni `git add .`.
- **Python:** `/home/fruiz/jax-platform/backend/.venv/bin/python` (se lee, no se modifica), con cwd `backend/` del worktree.
- **node_modules:** symlink `ln -s /home/fruiz/jax-platform/frontend/node_modules /home/fruiz/worktrees/jax-platform-tema/frontend/node_modules`. **Nunca se commitea.** Ojo: `.gitignore` dice `frontend/node_modules/`, con barra final, y ese patrón **no ignora un symlink**: `git status` lo muestra como no rastreado. Por eso se agrega archivo por archivo. En la Task 8 el symlink se reemplaza por un `npm ci` propio: `npm install` sobre el symlink escribiría en el checkout de producción.
- **Commits:** mensaje con asunto, línea en blanco, cuerpo, línea en blanco y trailers reales. Forma usada en todo el plan:
  ```bash
  git -C /home/fruiz/worktrees/jax-platform-tema commit -m "<asunto>" -m "<cuerpo>" -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
  ```
  El cuerpo del PR termina con:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB
  ```
- **i18n:** ninguna cadena visible nueva en PR 1 salvo que la revisión la pida; si aparece una, va en `src/i18n/es.js` **y** `src/i18n/en.js`.
- **Sin hardcoding:** ningún color fuera de `tokens.css`. El tema de respaldo `'dark'` del backend sale de `DEFAULT_CONFIG` (`api/admin/config_admin.py`), no se reescribe.
- **P10:** todo `except Exception` del backend lleva `# fail-soft: <razón>` en la misma línea (scanner `tests/test_no_fail_open_except.py`). El endpoint nuevo no atrapa nada: si la base falla, responde 500 y el cliente se queda con el último predeterminado conocido.
- **LAS CUATRO:** EXPLAIN de la consulta real en un test (índice); sin caché nuevo en backend (§5.2 del spec); `localStorage['jax_theme_default']` es el caché del cliente, invalidado en cada carga y al guardar; aiomysql; `font-display: swap`; tamaño de bundle y FCP/CLS antes y después de cada PR; prueba de carga de `/api/apariencia` con números en `/home/fruiz/jax/DEUDA.md` (Task 30). Sin número medido no hay GO.
- **Pisos de CI** (`.github/workflows/policy.yml`): `numPassedTests` (vitest, exacto), `PISO_PASSED` (job con DB), `JAX_CI_MIN_PASSED` (job sin DB). Se mueven: medidos el 2026-09-14, master `bfab4de` = **125 / 611 / 301** y la rama de la etapa 2 ya los lleva a **125 / 633 / 303** (su Task 4b todavía va a subir vitest). **Siempre se lee el valor vigente del archivo después del rebase** y se pone el número **medido**, con comentario del porqué.
- **impeccable:** el hook marca Inter como sobreusada. Es una DECISIÓN de Fernando: se registra con `impeccable hooks ignore-value` y el motivo `user confirmed: Fernando 2026-09-14` (Task 8). No se reabre.
- **Registro de medidas:** `/home/fruiz/worktrees/jax-platform-tema/.superpowers/sdd/tema-tokens-medidas.md`. `.superpowers/` está en `.gitignore` (verificado con `git check-ignore`). Ahí van, con fecha y hora, cada número de bundle, FCP/CLS y carga. La Task 30 los pasa a DEUDA.
- **Deploy del frontend** (`/home/fruiz/jax/CONTEXT.md` §7, "CORREGIDO 2026-09-14"): build → backup en la VM con `cp -a` y `diff -rq` → rsync a `/tmp/axioma-deploy/` en la VM dev (`ssh -p 58291 fruiz@172.16.20.11`) → `sudo rsync -a --delete --chown=www:www` a `/www/wwwroot/axioma-ia.io/`, con `--exclude .user.ini` **en los dos saltos** (sin eso, `--delete` falla con código 23: aaPanel deja `.user.ini` inmutable).

---

## Mapa de archivos

| Archivo | Responsabilidad | PR |
|---|---|---|
| `frontend/src/tema/tokens.css` | **Única** fuente de los valores: 47 tokens × 2 temas | 1 |
| `frontend/src/tema/tokens.js` | Nombres, matriz `PARES`, `EXENTOS_TEXTO`, `PERMITIDOS_CRUDOS`, `MIGRADOS`, `colorToken()`; `tokenDeFaceta()` en PR 2 | 1 (crece en 2-4) |
| `frontend/src/tema/contraste.js` | `contraste()`, `luminancia()`, `parsearTokens()` (puro) | 1 |
| `frontend/src/tema/contraste.test.js` | Completitud, contraste en los dos temas, control negativo, uso en archivos migrados | 1 (crece) |
| `frontend/src/tema/tailwind.test.js` | `tailwind.config.js` expone cada token | 1 |
| `frontend/src/tema/aplicarTema.js` | Regla elección > predeterminado > oscuro; aplicar al DOM | 1 |
| `frontend/src/tema/scriptTema.test.js` | El script de `index.html` y `aplicarTema` dejan el mismo DOM | 1 |
| `frontend/src/store/useTema.js` | Store zustand `{ theme, predeterminado, toggleTheme, fijarPredeterminado, sincronizarPredeterminado }`. Reemplaza a `store/useTheme.js` | 1 |
| `frontend/src/store/useTema.test.js` | Comportamiento del store | 1 |
| `frontend/index.html` | Script `#tema-inicial` en el `<head>` | 1, 4 |
| `frontend/tailwind.config.js` | Colores = tokens; `oro` pasa a tokens; `hal` se va en PR 4 | 1, 4 |
| `frontend/src/index.css` | Importa tokens; `body` y barras con tokens; la capa `html.light-mode` se achica por PR y se borra en PR 4 | 1-4 |
| `frontend/src/main.jsx` | Sin el IIFE de tema; importa Inter | 1 |
| `frontend/src/App.jsx` | Llama `sincronizarPredeterminado()` una vez al montar | 1 |
| `backend/api/apariencia.py` | `GET /api/apariencia` público, una clave | 1 |
| `backend/tests/test_apariencia.py` | 7 tests con DB + EXPLAIN | 1 |
| `backend/main.py` | Registra el router | 1 |
| Pantallas | Migración por grupos (tabla M) | 1-4 |

**Decisión de estructura (desvío chico del spec, marcado):** el spec §5.3 describe `useTema` como "misma interfaz" que el hook con estado local de hoy. Ahora hay **dos** consumidores (App sincroniza el predeterminado y BarraUsuario cambia el tema), y dos `useState` separados se desalinean. Por eso `useTema` es un store de zustand (ya es dependencia) con la misma interfaz `{ theme, toggleTheme }` más `predeterminado`. BarraUsuario no cambia su forma de uso.

---

## Tabla M — clases viejas → tokens (la usan las Tasks 9, 14-17, 20-23 y 26)

Sale del §7.1 del spec y se amplía con **todas** las clases medidas en el árbol (`bfab4de`). Si una clase del archivo no está acá, se aplica la regla del final y **nunca** un color suelto.

| Hoy | Token |
|---|---|
| `bg-hal-bg`, `bg-slate-950`, `bg-slate-900` de página o encabezado | `bg-fondo` |
| `bg-slate-900` de campo, fila o panel dentro de una tarjeta; `bg-slate-900/50`, `/60`, `/80` con texto encima | `bg-hundido` |
| `bg-slate-800`; `bg-slate-800/50` con texto | `bg-superficie` |
| `bg-slate-700`, `bg-slate-600` | `bg-superficie-2` (+ `text-texto` o `text-texto-fuerte` encima; nunca `texto-suave`/`tenue`/estado) |
| `hover:bg-slate-800`, `hover:bg-slate-800/30`, `hover:bg-slate-800/50` | `hover:bg-superficie` |
| `hover:bg-slate-700`, `hover:bg-slate-600`, `hover:bg-slate-750` | `hover:bg-superficie-2` + `hover:text-texto` si el texto en reposo era suave, tenue o de estado |
| `bg-black/60`, `bg-black/70`, `rgba(0,0,0,0.7)` (velo de modal) | `bg-fondo/70` |
| `bg-white` (con texto oscuro encima) | `bg-texto-fuerte` + `text-fondo` |
| `bg-gray-500`, `bg-gray-700` (punto de estado, sin texto) | `bg-texto-tenue` |
| `border-slate-800`, `border-slate-700`, `hover:border-slate-700` | `border-borde`, `hover:border-borde` |
| `divide-slate-800/50` | `divide-borde` |
| `border-slate-600` en campo o control | `border-borde-control` (si sólo separa: `border-borde`) |
| `focus:border-purple-500`, `focus:border-blue-500`, `border-blue-500` de foco | `focus:border-foco` |
| `focus-visible:ring-blue-500`, `ring-blue-500` | `focus-visible:ring-foco`, `ring-foco` |
| `text-slate-100` | `text-texto-fuerte` |
| `text-slate-200`, `text-slate-300`, `text-hal-text`, `hover:text-slate-200`, `hover:text-slate-300` | `text-texto`, `hover:text-texto` |
| `text-slate-400`, `hover:text-slate-400` | `text-texto-suave`, `hover:text-texto-suave` |
| `text-slate-500`, `text-slate-600`, `text-slate-700`, `placeholder-slate-600` | `text-texto-tenue`, `placeholder-texto-tenue` |
| `text-slate-900`, `text-black`, `color: '#000'`, `color: '#0f172a'` sobre blanco o color de faceta | `text-fondo` (sólo sobre `bg-texto-fuerte` o `bg-faceta-*`: pares declarados) |
| `text-white`, `color: 'white'` sobre sólido | `text-sobre-color` |
| `bg-purple-600` / `hover:bg-purple-700` | `bg-acento` / `hover:bg-acento-hover` |
| `border-purple-500` | `border-acento` |
| `border-purple-800/50` | `border-acento/40` (borde decorativo) |
| `text-purple-300`, `text-purple-400` | `text-acento-texto` |
| `bg-purple-900/40`, `bg-purple-900/50`, `bg-purple-950/20`, `hover:bg-purple-800/50` | `bg-acento-fondo`, `hover:bg-acento-fondo` |
| `bg-blue-600` / `hover:bg-blue-500` | `bg-accion` / `hover:bg-accion-hover` |
| `bg-blue-500`, `bg-blue-400`, `'#3b82f6'` (barra o punto sin texto) | `bg-accion` (barra), `bg-info` (punto) |
| `bg-blue-500/20` (sin texto) | `bg-info/20` |
| `bg-blue-900`, `bg-blue-900/40`, `hover:bg-blue-900/60` (con texto) | `bg-info-fondo`, `hover:bg-info-fondo` |
| `text-blue-200`, `text-blue-300`, `text-blue-400`, `hover:text-blue-300`, `hover:text-blue-400` | `text-info`, `hover:text-info` (si el reposo ya es `text-info`, el hover pasa a `hover:underline`) |
| `border-blue-400` / `border-blue-600` | `border-info` / `border-accion` |
| `text-red-200…500`, `hover:text-red-300`, `hover:text-red-400` | `text-peligro`, `hover:text-peligro` |
| `bg-red-900`, `bg-red-950`, `bg-red-900/30`, `/40`, `bg-red-950/30`, `hover:bg-red-900`, `hover:bg-red-900/60`, `hover:bg-red-800/50` | `bg-peligro-fondo`, `hover:bg-peligro-fondo` |
| `border-red-800`, `border-red-700`, `hover:border-red-700` | `border-peligro-borde`, `hover:border-peligro-borde` |
| `bg-red-500`, `bg-red-600` con texto blanco; `hover:bg-red-500`, `hover:bg-red-700` | `bg-peligro-solido`, `hover:bg-peligro-solido-hover` |
| `border-red-500`, `border-red-600`, `hover:border-red-600` | `border-peligro-solido`, `hover:border-peligro-solido` |
| `bg-red-400` (punto) | `bg-peligro` |
| `text-green-200…400`, `text-emerald-400` | `text-exito` |
| `bg-green-900`, `bg-green-900/30`, `/40`, `bg-green-950/30`, `hover:bg-green-800/50` | `bg-exito-fondo`, `hover:bg-exito-fondo` |
| `border-green-800` / `border-green-600` | `border-exito-borde` / `border-exito` |
| `bg-green-400` (punto) | `bg-exito` |
| `text-amber-200`, `text-amber-400`, `text-amber-500/80`, `text-yellow-400`, `text-orange-300`, `text-orange-400` | `text-aviso` |
| `bg-amber-900`, `bg-orange-900/40`, `bg-orange-950/10`, `hover:bg-orange-900/60` | `bg-aviso-fondo`, `hover:bg-aviso-fondo` |
| `border-amber-600`, `border-orange-500` (aviso) | `border-aviso-borde` |
| `bg-amber-500`, `bg-yellow-500`, `hover:bg-amber-400` (punto o barra sin texto) | `bg-aviso`; **con texto encima**: `bg-aviso-fondo text-aviso` |
| `bg-orange-600`, `'#f97316'` (modo comando) | `bg-modo-comando` + `text-sobre-color`; borde `border-modo-comando/50` |
| `bg-orange-500/20` (sin texto) | `bg-modo-comando/20` |
| `text-oro`, `text-oro-claro`, `text-oro-oscuro` | igual (ahora son tokens) |
| SVG `fill="#0f172a"`, `fill="#0a0f1a"` / `stroke="#1e293b"` / `fill="white"` | `className="fill-fondo"` / `stroke-superficie` / `fill-sobre-color` |

**Regla para lo que no está en la tabla** (spec §7.1): el token de estado más cercano. Si ninguno sirve, se agrega un token nuevo a `tokens.css` (los dos temas) y a `TOKENS`, **con su par en `PARES`**, en el mismo commit. Un fondo translúcido (`/NN`) sólo va donde no hay texto encima: el test mide contra fondos opacos.

**Verificación mecánica por grupo** (además del test): con cwd `frontend/src`, sobre los archivos del grupo:

```bash
grep -nE "(^|[^a-z-])(bg|text|border|divide|ring|placeholder|fill|stroke|from|via|to|outline|accent|caret|decoration|shadow)-((slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}|white|black)|(bg|text|border)-hal-|#[0-9a-fA-F]{3,8}([^0-9a-zA-Z]|$)|rgba?\( *[0-9]|(^|[^a-z-])dark:[a-z]|(fill|stroke)=[\"'](white|black)" <archivos>
```

Expected: **sin salida.** La lista de excepciones permitidas es `PERMITIDOS_CRUDOS` en `tokens.js`: cerrada, vacía al empezar, y cada entrada lleva `{ archivo, texto, motivo }`. El test la respeta; el grep muestra también esas entradas, y cada una se revisa a mano contra la lista.

---

# PR 1 · Base, Login y barra de usuario (Tasks 1-12)

### Task 1: Punto de partida — orden con la etapa 2, symlink y línea de base

**Files:** ninguno del repo. Crea el registro `.superpowers/sdd/tema-tokens-medidas.md`, que está ignorado.

- [ ] **Step 1: Estado de la etapa 2**

```bash
git -C /home/fruiz/worktrees/jax-platform-tema fetch origin
gh pr list --repo fjruizhn/jax-platform --head feat/admin-usuarios-etapa2-sesiones --state all --json number,state,mergeCommit
git -C /home/fruiz/worktrees/jax-platform-tema log --oneline -3 origin/master
```

- [ ] **Step 2a: si la etapa 2 está MERGED:** rebasar y seguir con las Tasks en orden.

```bash
git -C /home/fruiz/worktrees/jax-platform-tema branch --show-current   # feat/tema-tokens
git -C /home/fruiz/worktrees/jax-platform-tema rebase origin/master
git -C /home/fruiz/worktrees/jax-platform-tema log --oneline -3
```
Expected: `f98fe9f` reescrito arriba de master (sólo toca `docs/`, sin conflictos).

- [ ] **Step 2b: si la etapa 2 NO está mergeada:** no se rebasa todavía. Se trabaja sobre la base actual y **se dejan para el final** los archivos que choca con la etapa 2: `pages/Login.jsx` (Task 9 lo migra último dentro de su task), `backend/main.py` (Task 6: sólo dos líneas nuevas) y `.github/workflows/policy.yml` (Task 10). El PR 1 **no se abre** hasta que la etapa 2 esté en master. Entonces:

```bash
git -C /home/fruiz/worktrees/jax-platform-tema fetch origin
git -C /home/fruiz/worktrees/jax-platform-tema rebase origin/master
```
Cómo resolver cada conflicto:
- `frontend/src/pages/Login.jsx`: se conserva **todo** lo de la etapa 2 (lectura de `avisoSesion`, su `<AlertaError>`, el borrado al iniciar sesión) y se aplica la tabla M a las clases de color que queden. `AlertaError` ya pinta con `text-peligro` (Task 9), así que el aviso de la 4b queda con tokens sin tocar su lógica.
- `backend/main.py`: se conservan las líneas de la etapa 2 (`HTTPException`, `verificar_sesion`, `logger`) y se agregan las dos de la Task 6 (import y `include_router`).
- `.github/workflows/policy.yml`: se toma el archivo de master (los números de la etapa 2) y se rehace la Task 10 Step 3 sobre esos valores, midiendo de nuevo.
- `frontend/src/i18n/*.js`, `api/client.js`, `store/useJaxStore.js`: el PR 1 no los toca. Si aparecen en conflicto, algo está mal: parar y mirar.

Después del rebase se corren de nuevo las tres suites (Task 10 Step 1) antes de abrir el PR.

- [ ] **Step 3: Symlink de node_modules y verificación de que no se va a commitear**

```bash
ln -s /home/fruiz/jax-platform/frontend/node_modules /home/fruiz/worktrees/jax-platform-tema/frontend/node_modules
git -C /home/fruiz/worktrees/jax-platform-tema status --short
```
Expected: aparece `?? frontend/node_modules` (el patrón con barra no cubre un symlink). Nunca se agrega.

- [ ] **Step 4: Suites de partida (verde antes de tocar nada)**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -4
cd /home/fruiz/worktrees/jax-platform-tema/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q 2>&1 | tail -2
cd /home/fruiz/worktrees/jax-platform-tema/backend && JAX_CI_NO_DB=1 /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q 2>&1 | tail -2
grep -n 'numPassedTests !==\|PISO_PASSED = \|JAX_CI_MIN_PASSED:' /home/fruiz/worktrees/jax-platform-tema/.github/workflows/policy.yml
```
Expected: los tres conteos **iguales** a los pisos del archivo, 0 fallidos. Anotar los seis números en el registro. Si no coinciden, parar: la base está rota o el piso está viejo.

- [ ] **Step 5: Línea de base de bundle**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm run build >/dev/null && \
for f in dist/assets/index-*.js dist/assets/index-*.css; do printf '%s %s B crudo, %s B gzip-9\n' "$f" "$(stat -c %s "$f")" "$(gzip -9 -c "$f" | wc -c)"; done; \
ls -l dist/assets/*.woff2 dist/assets/*.woff 2>/dev/null | awk '{s+=$5; print $5, $9} END {print "fuentes total", s}'
```
Expected: del orden de §1.2 del spec (JS ~551.634 B, CSS ~29.102 B; varía con la etapa 2). Anotar en el registro con fecha y el sha de HEAD.

- [ ] **Step 6: Línea de base de primer pintado (FCP y CLS del Login, en frío)**

Lighthouse por `npx` (no queda instalado; si Fernando prefiere no bajarlo, se usa el panel Performance de Chrome y se anota el método). Con cwd `frontend`:

```bash
SCR=/tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad
npx vite preview --port 4173 --strictPort    # en segundo plano (run_in_background)
npx --yes lighthouse@12 http://127.0.0.1:4173/login --only-categories=performance --preset=desktop \
  --chrome-flags="--headless=new" --output=json --output-path=$SCR/lh-oscuro-base.json --quiet
node -e 'const r=require(process.argv[1]);console.log("FCP",Math.round(r.audits["first-contentful-paint"].numericValue),"ms CLS",r.audits["cumulative-layout-shift"].numericValue.toFixed(3))' $SCR/lh-oscuro-base.json
```
**Claro:** la base todavía no tiene `jax_theme_default`. Se siembra un perfil de Chrome: se copia `dist` a `$SCR/dist-medicion`, se agrega `$SCR/dist-medicion/sembrar.html` con `<script>localStorage.setItem('jax_theme','light')</script>`, se sirve con `npx vite preview --outDir $SCR/dist-medicion --port 4174` y:

```bash
google-chrome --headless=new --user-data-dir=$SCR/perfil-claro --virtual-time-budget=2000 --dump-dom http://127.0.0.1:4174/sembrar.html >/dev/null
google-chrome --headless=new --user-data-dir=$SCR/perfil-claro --virtual-time-budget=3000 --dump-dom http://127.0.0.1:4174/login | grep -c 'light-mode'
npx --yes lighthouse@12 http://127.0.0.1:4174/login --only-categories=performance --preset=desktop --disable-storage-reset \
  --chrome-flags="--headless=new --user-data-dir=$SCR/perfil-claro" --output=json --output-path=$SCR/lh-claro-base.json --quiet
```
Expected: el `grep -c` da ≥1 (el perfil está sembrado). **Incertidumbre declarada:** no verifiqué que chrome-launcher respete `--user-data-dir` pasado en `--chrome-flags`. Si la corrida en claro da idéntica a la oscura, o hay dudas, se mide claro con el panel Performance de Chrome a mano (Network sin caché, recarga) y se anota así. Correr cada medición **3 veces** y anotar la mediana. Parar los `vite preview`.

Sin commit.

---

### Task 2: Tokens, matriz de pares y test de contraste

**Files:**
- Create: `frontend/src/tema/tokens.css`, `frontend/src/tema/tokens.js`, `frontend/src/tema/contraste.js`
- Test: `frontend/src/tema/contraste.test.js`

**Interfaces:**
- Produces: `TOKENS: string[]` (47), `PARES: [string, string, number][]` (88), `AA_TEXTO = 4.5`, `AA_UI = 3`, `EXENTOS_TEXTO: { [token]: { motivo, archivos: string[] } }`, `PERMITIDOS_CRUDOS: { archivo, texto, motivo }[]`, `MIGRADOS: string[]` (rutas relativas a `src/`), `colorToken(nombre: string, alfa = 1): string`; `contraste(a: number[3], b: number[3]): number`, `luminancia(rgb): number`, `parsearTokens(css): { oscuro: {[n]: number[3]}, claro: {...} }`.

- [ ] **Step 1: Escribir el test (RED)**

`frontend/src/tema/contraste.test.js`:

```js
// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { parsearTokens, contraste } from './contraste.js'
import { TOKENS, PARES, AA_TEXTO, colorToken } from './tokens.js'

// Test de contraste del tema (spec 2026-09-14-tema-tokens-design.md §6).
// Reemplaza a lightModeOverrides.test.js, que exigía que un override
// EXISTIERA, no que se leyera. Lee tokens.css por fs, en entorno node: vitest
// excluye el CSS del pipeline y un `?raw` llega vacío (medido en
// lightModeOverrides.test.js).
const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')
const temas = parsearTokens(css)

function fallas(tema) {
  return PARES.flatMap(([frente, fondo, minimo]) => {
    const r = contraste(temas[tema][frente], temas[tema][fondo])
    return r >= minimo ? [] : [`${frente} sobre ${fondo} en ${tema}: ${r.toFixed(2)} < ${minimo}`]
  })
}

describe('tokens de color', () => {
  it('tokens.css tiene los dos temas con 47 tokens cada uno', () => {
    expect(Object.keys(temas.oscuro)).toHaveLength(47)
    expect(Object.keys(temas.claro)).toHaveLength(47)
  })

  it('todo token de tokens.js tiene valor en los dos temas, y todo valor tiene nombre', () => {
    const faltan = TOKENS.flatMap((n) => ['oscuro', 'claro'].filter((t) => !temas[t][n]).map((t) => `${n} (${t})`))
    const nombres = new Set([...Object.keys(temas.oscuro), ...Object.keys(temas.claro)])
    const sinNombre = [...nombres].filter((n) => !TOKENS.includes(n))
    expect(faltan).toEqual([])
    expect(sinNombre).toEqual([])
  })

  it('control negativo: el gris viejo (slate-500) sobre el fondo oscuro falla AA', () => {
    // Si la función de contraste no falla con esto, el resto del test no valida nada.
    const r = contraste([100, 116, 139], temas.oscuro.fondo)
    expect(r).toBeCloseTo(3.75, 1)
    expect(r).toBeLessThan(AA_TEXTO)
  })

  it('los 88 pares alcanzan su mínimo en oscuro', () => {
    expect(PARES).toHaveLength(88)
    expect(fallas('oscuro')).toEqual([])
  })

  it('los 88 pares alcanzan su mínimo en claro', () => {
    expect(fallas('claro')).toEqual([])
  })

  it('colorToken arma rgb(var()) y un nombre desconocido cae en texto-suave', () => {
    expect(colorToken('faceta-hyde')).toBe('rgb(var(--faceta-hyde) / 1)')
    expect(colorToken('peligro', 0.12)).toBe('rgb(var(--peligro) / 0.12)')
    expect(colorToken('no-existe')).toBe('rgb(var(--texto-suave) / 1)')
  })
})
```

- [ ] **Step 2: Verlo fallar**

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/contraste.test.js 2>&1 | tail -8`
Expected: FAIL. El archivo no carga: `Failed to load url ./contraste.js` (o `ENOENT ... tokens.css`).

- [ ] **Step 3: Escribir `contraste.js`**

```js
// Contraste WCAG 2.x (luminancia relativa sRGB). Puro: lo usa el test de
// contraste; la app no lo carga en tiempo de ejecución.
function canal(c) {
  const s = c / 255
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
}

export function luminancia([r, g, b]) {
  return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)
}

export function contraste(a, b) {
  const [claro, oscuro] = [luminancia(a), luminancia(b)].sort((x, y) => y - x)
  return (claro + 0.05) / (oscuro + 0.05)
}

// `--nombre: R G B;` de un bloque de CSS -> { nombre: [R, G, B] }.
function variables(bloque) {
  const salida = {}
  for (const [, nombre, r, g, b] of bloque.matchAll(/--([a-z0-9-]+):\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*;/g)) {
    salida[nombre] = [Number(r), Number(g), Number(b)]
  }
  return salida
}

// tokens.css -> { oscuro, claro }. Oscuro es `:root { }` y claro
// `:root[data-tema="claro"] { }`. Si falta un bloque, tira: un tema a medias
// no puede pasar en verde.
export function parsearTokens(css) {
  const oscuro = css.match(/:root\s*\{([^}]*)\}/)
  const claro = css.match(/:root\[data-tema="claro"\]\s*\{([^}]*)\}/)
  if (!oscuro || !claro) throw new Error('tokens.css sin el bloque :root o sin :root[data-tema="claro"]')
  return { oscuro: variables(oscuro[1]), claro: variables(claro[1]) }
}
```

- [ ] **Step 4: Escribir `tokens.css`** (valores del §3.2 del spec, con las facetas en el tono 400 en oscuro según la respuesta 1 del dueño)

```css
/* Tokens de color del tema (spec 2026-09-14-tema-tokens-design.md §3).
   ÚNICA fuente de verdad de los valores: Tailwind y el test de contraste los
   leen de acá. Canales RGB separados por espacio para que Tailwind aplique
   opacidad: rgb(var(--fondo) / <alpha-value>).
   Oscuro = :root (el tema de hoy y el DEFAULT_CONFIG). Claro = data-tema. */
:root {
  color-scheme: dark;
  --fondo: 15 23 42;
  --superficie: 30 41 59;
  --superficie-2: 51 65 85;
  --hundido: 15 23 42;
  --borde: 51 65 85;
  --borde-control: 99 115 138;
  --barra-pista: 30 41 59;
  --barra-pulgar: 51 65 85;
  --texto-fuerte: 241 245 249;
  --texto: 226 232 240;
  --texto-suave: 148 163 184;
  --texto-tenue: 129 144 166;
  --sobre-color: 255 255 255;
  --acento: 147 51 234;
  --acento-hover: 126 34 206;
  --acento-texto: 192 132 252;
  --acento-fondo: 53 36 89;
  --accion: 37 99 235;
  --accion-hover: 29 78 216;
  --info: 96 165 250;
  --info-fondo: 30 46 83;
  --foco: 59 130 246;
  --peligro: 248 113 113;
  --peligro-fondo: 59 37 50;
  --peligro-borde: 153 27 27;
  --peligro-solido: 220 38 38;
  --peligro-solido-hover: 185 28 28;
  --exito: 74 222 128;
  --exito-fondo: 27 54 55;
  --exito-borde: 22 101 52;
  --aviso: 251 191 36;
  --aviso-fondo: 57 45 46;
  --aviso-borde: 146 64 14;
  --oro: 201 168 76;
  --oro-claro: 232 213 163;
  --oro-oscuro: 138 109 47;
  --burbuja-usuario: 29 56 92;
  --modo-comando: 194 65 12;
  --faceta-jax-local: 96 165 250;
  --faceta-jekyll: 129 140 248;
  --faceta-hyde: 249 115 22;
  --faceta-hipatia: 16 185 129;
  --faceta-thot: 245 158 11;
  --faceta-kimi: 6 182 212;
  --faceta-ada: 167 139 250;
  --faceta-jacobs: 255 255 255;
  --faceta-imagen: 167 139 250;
}

:root[data-tema="claro"] {
  color-scheme: light;
  --fondo: 248 250 252;
  --superficie: 241 245 249;
  --superficie-2: 226 232 240;
  --hundido: 255 255 255;
  --borde: 203 213 225;
  --borde-control: 127 143 165;
  --barra-pista: 226 232 240;
  --barra-pulgar: 148 163 184;
  --texto-fuerte: 2 6 23;
  --texto: 15 23 42;
  --texto-suave: 71 85 105;
  --texto-tenue: 97 113 136;
  --sobre-color: 255 255 255;
  --acento: 147 51 234;
  --acento-hover: 126 34 206;
  --acento-texto: 126 34 206;
  --acento-fondo: 250 245 255;
  --accion: 37 99 235;
  --accion-hover: 29 78 216;
  --info: 29 78 216;
  --info-fondo: 239 246 255;
  --foco: 59 130 246;
  --peligro: 185 28 28;
  --peligro-fondo: 254 242 242;
  --peligro-borde: 252 165 165;
  --peligro-solido: 220 38 38;
  --peligro-solido-hover: 185 28 28;
  --exito: 21 128 61;
  --exito-fondo: 240 253 244;
  --exito-borde: 134 239 172;
  --aviso: 180 83 9;
  --aviso-fondo: 255 251 235;
  --aviso-borde: 252 211 77;
  --oro: 122 95 36;
  --oro-claro: 137 108 47;
  --oro-oscuro: 160 138 85;
  --burbuja-usuario: 219 234 254;
  --modo-comando: 194 65 12;
  --faceta-jax-local: 29 78 216;
  --faceta-jekyll: 67 56 202;
  --faceta-hyde: 194 65 12;
  --faceta-hipatia: 4 120 87;
  --faceta-thot: 180 83 9;
  --faceta-kimi: 14 116 144;
  --faceta-ada: 109 40 217;
  --faceta-jacobs: 15 23 42;
  --faceta-imagen: 109 40 217;
}
```

- [ ] **Step 5: Escribir `tokens.js`**

```js
// Nombres de los tokens de color y matriz de pares de contraste (spec
// 2026-09-14-tema-tokens-design.md §3-§4). Acá NO hay valores: viven sólo en
// tokens.css. Lo importan tailwind.config.js, el test de contraste y el código
// que pinta un color que viene de datos (colorToken).

export const TOKENS = [
  'fondo', 'superficie', 'superficie-2', 'hundido', 'borde', 'borde-control',
  'barra-pista', 'barra-pulgar',
  'texto-fuerte', 'texto', 'texto-suave', 'texto-tenue', 'sobre-color',
  'acento', 'acento-hover', 'acento-texto', 'acento-fondo',
  'accion', 'accion-hover', 'info', 'info-fondo', 'foco',
  'peligro', 'peligro-fondo', 'peligro-borde', 'peligro-solido', 'peligro-solido-hover',
  'exito', 'exito-fondo', 'exito-borde',
  'aviso', 'aviso-fondo', 'aviso-borde',
  'oro', 'oro-claro', 'oro-oscuro',
  'burbuja-usuario', 'modo-comando',
  'faceta-jax-local', 'faceta-jekyll', 'faceta-hyde', 'faceta-hipatia',
  'faceta-thot', 'faceta-kimi', 'faceta-ada', 'faceta-jacobs', 'faceta-imagen',
]

// WCAG 2.x AA: 4,5 texto normal; 3 componentes de UI (1.4.11).
export const AA_TEXTO = 4.5
export const AA_UI = 3

const FACETAS = TOKENS.filter((t) => t.startsWith('faceta-'))
const TEXTOS_SOBRE_BASE = [
  'texto-fuerte', 'texto', 'texto-suave', 'texto-tenue', 'acento-texto',
  'info', 'peligro', 'exito', 'aviso', 'oro', 'oro-claro', ...FACETAS,
]
const FONDOS_BASE = ['fondo', 'superficie', 'hundido']

// [primer plano, fondo, mínimo]. 88 pares (spec §3.3). Lo que no está acá no
// se combina: p. ej. texto-suave o un color de estado sobre superficie-2.
export const PARES = [
  ...FONDOS_BASE.flatMap((f) => TEXTOS_SOBRE_BASE.map((t) => [t, f, AA_TEXTO])),
  ['texto-fuerte', 'superficie-2', AA_TEXTO],
  ['texto', 'superficie-2', AA_TEXTO],
  ...['acento', 'info', 'peligro', 'exito', 'aviso'].flatMap((e) => [
    [e === 'acento' ? 'acento-texto' : e, `${e}-fondo`, AA_TEXTO],
    ['texto', `${e}-fondo`, AA_TEXTO],
  ]),
  ...['acento', 'acento-hover', 'accion', 'accion-hover', 'peligro-solido', 'peligro-solido-hover', 'modo-comando']
    .map((f) => ['sobre-color', f, AA_TEXTO]),
  ['texto', 'burbuja-usuario', AA_TEXTO],
  ['texto-suave', 'burbuja-usuario', AA_TEXTO],
  ['fondo', 'texto-fuerte', AA_TEXTO],
  ...FONDOS_BASE.flatMap((f) => [['borde-control', f, AA_UI], ['foco', f, AA_UI]]),
]

// Tokens que se pueden usar como `text-*` sin ser primer plano de ningún par.
// Lista CERRADA: cada entrada con su motivo y los únicos archivos donde vale.
export const EXENTOS_TEXTO = {
  'oro-oscuro': {
    motivo: 'separador aria-hidden del logotipo: decorativo, WCAG 1.4.3 lo exime',
    archivos: ['components/LogoAxioma.jsx'],
  },
}

// Colores crudos (clase de paleta, hex, rgb) que se aceptan en un archivo
// migrado. Lista CERRADA, vacía a propósito: cada entrada lleva
// { archivo, texto, motivo } y la aprueba la revisión.
export const PERMITIDOS_CRUDOS = []

// Archivos (relativos a src/) que ya pintan sólo con tokens. Cada PR del
// rollout amplía la lista; el PR 4 la reemplaza por "todos".
export const MIGRADOS = []

const RESPALDO = 'texto-suave'

// Color CSS de un token para un `style={{}}` (colores que vienen de datos).
// Un nombre desconocido cae en texto-suave, el gris de respaldo de hoy.
export function colorToken(nombre, alfa = 1) {
  const token = TOKENS.includes(nombre) ? nombre : RESPALDO
  return `rgb(var(--${token}) / ${alfa})`
}
```

(`MIGRADOS` arranca vacío. La Task 9 lo llena junto con el test que lo usa.)

- [ ] **Step 6: Verlo pasar**

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/contraste.test.js 2>&1 | tail -4`
Expected: `6 passed`. (Verificado al escribir este plan con el mismo código: 47/47/47 tokens, 88 pares únicos, 0 fallas; mínimos 3,03 en oscuro y 3,01 en claro, los dos de `borde-control` sobre `superficie`; control 3,75.)

- [ ] **Step 7: Control negativo visto en ROJO (mutación sobre una copia real, no `git checkout`)**

```bash
SCR=/tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad
cp /home/fruiz/worktrees/jax-platform-tema/frontend/src/tema/tokens.css $SCR/tokens.css.bak
sed -i '0,/--texto-tenue: 129 144 166;/s//--texto-tenue: 100 116 139;/' /home/fruiz/worktrees/jax-platform-tema/frontend/src/tema/tokens.css
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/contraste.test.js 2>&1 | grep -E 'texto-tenue sobre|failed|passed'
cp $SCR/tokens.css.bak /home/fruiz/worktrees/jax-platform-tema/frontend/src/tema/tokens.css
diff $SCR/tokens.css.bak /home/fruiz/worktrees/jax-platform-tema/frontend/src/tema/tokens.css && echo RESTAURADO
```
Expected: `1 failed`, con `texto-tenue sobre fondo en oscuro: 3.75 < 4.5` (y los de `superficie` y `hundido`). Después `RESTAURADO` y, al correr de nuevo, `6 passed`. Repetir borrando una línea de `--faceta-kimi` en el bloque claro: tiene que fallar la completitud con `faceta-kimi (claro)`. Restaurar igual.

- [ ] **Step 8: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/tema/tokens.css frontend/src/tema/tokens.js frontend/src/tema/contraste.js frontend/src/tema/contraste.test.js
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): 47 tokens de color en tokens.css y test de contraste AA de 88 pares en los dos temas" -m "Spec 2026-09-14-tema-tokens §3 y §6. Los valores viven sólo en tokens.css; tokens.js tiene nombres, pares y colorToken(). El test calcula el contraste WCAG de cada par en oscuro y en claro, exige que cada token tenga valor en los dos temas y trae un control negativo (slate-500 sobre el fondo = 3,75). Visto en rojo mutando texto-tenue y borrando un token." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---

### Task 3: Tailwind mapea los tokens; `index.css` los importa

**Files:**
- Modify: `frontend/tailwind.config.js`, `frontend/src/index.css`
- Test: `frontend/src/tema/tailwind.test.js`

**Interfaces:**
- Consumes: `TOKENS` (Task 2).
- Produces: clases `bg-*`, `text-*`, `border-*`, `divide-*`, `ring-*`, `placeholder-*`, `fill-*`, `stroke-*` para cada token, con opacidad (`bg-fondo/70`).

- [ ] **Step 1: Test (RED)**

`frontend/src/tema/tailwind.test.js`:

```js
// @vitest-environment node
import { it, expect } from 'vitest'
import config from '../../tailwind.config.js'
import { TOKENS } from './tokens.js'

// Cada token es un color de Tailwind con opacidad, leído de la variable CSS.
// Si alguien agrega un token a tokens.js y no llega a Tailwind, esto falla.
it('tailwind.config.js expone cada token como rgb(var(--x) / <alpha-value>)', () => {
  const colores = config.theme.extend.colors
  const mal = TOKENS.filter((n) => colores[n] !== `rgb(var(--${n}) / <alpha-value>)`)
  expect(mal).toEqual([])
  // El `oro` de antes era un objeto con hex; ahora es el token.
  expect(colores.oro).toBe('rgb(var(--oro) / <alpha-value>)')
})
```

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/tailwind.test.js 2>&1 | tail -8`
Expected: FAIL; `mal` lista los 47 nombres y `colores.oro` es un objeto.

- [ ] **Step 2: `tailwind.config.js`**

Reemplazar el bloque `colors` (hoy `oro` con hex y `hal`) por:

```js
import { TOKENS } from './src/tema/tokens.js'

// Tokens del tema (spec 2026-09-14-tema-tokens §4): un color por token, con
// opacidad. Los valores están en src/tema/tokens.css; acá sólo los nombres.
const token = (nombre) => `rgb(var(--${nombre}) / <alpha-value>)`
```

y dentro de `theme.extend`:

```js
      colors: {
        ...Object.fromEntries(TOKENS.map((n) => [n, token(n)])),
        // Colores viejos del panel: se van en el PR 4 del rollout, cuando
        // Dashboard migra (hoy los usa con bg-hal-bg y text-hal-text).
        hal: {
          bg: '#0f172a',
          panel: '#1e293b',
          border: '#334155',
          text: '#e2e8f0',
          muted: '#64748b',
        },
      },
```

El comentario de marca de `fontFamily` queda igual; el que decía "los mismos dorados" apunta ahora a `tokens.css`.

- [ ] **Step 3: `index.css`**

Arriba del todo, antes de `@tailwind`:

```css
/* Tokens del tema: única fuente de los valores de color (src/tema/tokens.css). */
@import './tema/tokens.css';
```

`body` y las barras pasan a tokens:

```css
body {
  background-color: rgb(var(--fondo));
  color: rgb(var(--texto));
  font-family: 'Inter', 'Segoe UI', sans-serif;
  margin: 0;
  padding: 0;
  overflow: hidden;
}
```
```css
::-webkit-scrollbar-track {
  background: rgb(var(--barra-pista));
}

::-webkit-scrollbar-thumb {
  background: rgb(var(--barra-pulgar));
  border-radius: 3px;
}
```

Se **borran** (los resuelven los tokens con `data-tema`): `html.light-mode body { … }`, `html.light-mode ::-webkit-scrollbar-track { … }`, `html.light-mode ::-webkit-scrollbar-thumb { … }` y el bloque final "Marca Axioma en modo claro" (`.text-oro`, `.text-oro-claro`, `.text-oro-oscuro`). Arriba de lo que queda de la capa se pone:

```css
/* ===================== LIGHT MODE (capa vieja) =====================
   Overrides clase por clase de las pantallas que TODAVÍA no migraron a tokens
   (spec 2026-09-14-tema-tokens §8). Se achica en cada PR del rollout y se
   borra entera en el PR 4. No agregar overrides nuevos: migrar a tokens. */
```

- [ ] **Step 4: Verde y build**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema 2>&1 | tail -3
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm run build >/dev/null && grep -o -- '--fondo:[^;]*' dist/assets/index-*.css && grep -c 'rgb(var(--oro)' dist/assets/index-*.css
```
Expected: `7 passed`; dos líneas `--fondo:15 23 42` y `--fondo:248 250 252`; `rgb(var(--oro)` al menos 1 (LogoAxioma usa `text-oro`).

- [ ] **Step 5: Suite completa y commit**

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3` → todo verde (base + 7).

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/tailwind.config.js frontend/src/index.css frontend/src/tema/tailwind.test.js
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): Tailwind mapea los 47 tokens; body, barras y oro pasan a tokens" -m "Spec §4. Cada token es rgb(var(--x) / <alpha-value>), así que bg-superficie/50 funciona. index.css importa tokens.css; se borran los overrides de light-mode de body, barras de desplazamiento y oro, que ahora resuelve data-tema. hal queda hasta el PR 4." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---

### Task 4: `aplicarTema` y el script de `index.html` antes del primer pintado

**Files:**
- Create: `frontend/src/tema/aplicarTema.js`
- Modify: `frontend/index.html`, `frontend/src/main.jsx:7-11` (se borra el IIFE)
- Test: `frontend/src/tema/scriptTema.test.js`

**Interfaces:**
- Produces: `CLAVE_ELECCION = 'jax_theme'`, `CLAVE_PREDETERMINADO = 'jax_theme_default'`, `TEMA_DE_RESPALDO = 'dark'`, `esTema(v): boolean`, `resolverTema(eleccion, predeterminado): 'dark'|'light'`, `temaInicial(almacen = localStorage)`, `aplicarTema(tema, raiz = document.documentElement)`.

- [ ] **Step 1: Test (RED)**

`frontend/src/tema/scriptTema.test.js`:

```js
// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { temaInicial, aplicarTema } from './aplicarTema.js'

// El script en línea de index.html es la copia mínima de temaInicial +
// aplicarTema (spec §5.3): corre antes del primer pintado y no puede importar
// módulos. Este test lo extrae y lo corre con un DOM y un localStorage falsos,
// y exige que deje el MISMO DOM que el módulo, en cada combinación.
const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
const script = html.match(/<script id="tema-inicial">([\s\S]*?)<\/script>/)?.[1]

function almacen(valores) {
  return { getItem: (k) => (k in valores ? valores[k] : null) }
}

function raizFalsa() {
  const attrs = {}
  const clases = new Set()
  return {
    attrs, clases,
    setAttribute: (k, v) => { attrs[k] = v },
    removeAttribute: (k) => { delete attrs[k] },
    classList: { add: (c) => clases.add(c), remove: (c) => clases.delete(c) },
  }
}

const foto = (r) => ({ attrs: { ...r.attrs }, clases: [...r.clases].sort() })

const CASOS = [
  ['sin elección ni predeterminado', {}, 'dark'],
  ['elección claro', { jax_theme: 'light' }, 'light'],
  ['sin elección, predeterminado claro', { jax_theme_default: 'light' }, 'light'],
  ['elección oscuro gana al predeterminado claro', { jax_theme: 'dark', jax_theme_default: 'light' }, 'dark'],
  ['elección inválida cae al predeterminado', { jax_theme: 'violeta', jax_theme_default: 'light' }, 'light'],
]

describe('script de tema en index.html', () => {
  it('existe y va antes de cualquier link, style o script de módulo', () => {
    expect(script).toBeTruthy()
    const head = html.slice(0, html.indexOf('</head>'))
    const pos = head.indexOf('id="tema-inicial"')
    for (const tag of ['<link', '<style', '<script type="module"']) {
      const otro = head.indexOf(tag)
      if (otro !== -1) expect(pos).toBeLessThan(otro)
    }
  })

  it.each(CASOS)('%s: el script y aplicarTema dejan el mismo DOM', (_, valores, esperado) => {
    const deScript = raizFalsa()
    new Function('localStorage', 'document', script)(almacen(valores), { documentElement: deScript })
    const deModulo = raizFalsa()
    aplicarTema(temaInicial(almacen(valores)), deModulo)
    expect(foto(deScript)).toEqual(foto(deModulo))
    // Que los dos coincidan no alcanza: tienen que coincidir en lo correcto.
    expect(deModulo.attrs['data-tema']).toBe(esperado === 'light' ? 'claro' : undefined)
  })
})
```

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/scriptTema.test.js 2>&1 | tail -6`
Expected: FAIL; `Failed to load url ./aplicarTema.js`.

- [ ] **Step 2: `aplicarTema.js`**

```js
// Tema claro/oscuro (spec 2026-09-14-tema-tokens §5). Una sola regla y una
// sola forma de aplicarlo al DOM. El script en línea de index.html es la copia
// mínima de temaInicial + aplicarTema; scriptTema.test.js exige que hagan lo mismo.

export const CLAVE_ELECCION = 'jax_theme' // lo que eligió el usuario (clave de siempre)
export const CLAVE_PREDETERMINADO = 'jax_theme_default' // último theme_default conocido
export const TEMA_DE_RESPALDO = 'dark' // = DEFAULT_CONFIG.theme_default del backend

export function esTema(valor) {
  return valor === 'dark' || valor === 'light'
}

// Si hay elección, gana la elección; si no, el predeterminado; si no, oscuro.
export function resolverTema(eleccion, predeterminado) {
  if (esTema(eleccion)) return eleccion
  if (esTema(predeterminado)) return predeterminado
  return TEMA_DE_RESPALDO
}

export function temaInicial(almacen = localStorage) {
  return resolverTema(almacen.getItem(CLAVE_ELECCION), almacen.getItem(CLAVE_PREDETERMINADO))
}

// data-tema="claro" es lo que leen los tokens. La clase light-mode es la de la
// capa vieja de index.css: convive hasta el PR 4 del rollout, que la quita.
export function aplicarTema(tema, raiz = document.documentElement) {
  if (tema === 'light') {
    raiz.setAttribute('data-tema', 'claro')
    raiz.classList.add('light-mode')
  } else {
    raiz.removeAttribute('data-tema')
    raiz.classList.remove('light-mode')
  }
}
```

- [ ] **Step 3: `index.html`** — el script va inmediatamente después de `<meta charset>`, antes del `<link rel="icon">`:

```html
    <meta charset="UTF-8" />
    <!-- Tema antes del primer pintado (spec 2026-09-14-tema-tokens §5.2).
         Copia mínima de src/tema/aplicarTema.js (temaInicial + aplicarTema);
         si cambia uno, cambia el otro: src/tema/scriptTema.test.js los compara. -->
    <script id="tema-inicial">
      (function () {
        var e = localStorage.getItem('jax_theme')
        var p = localStorage.getItem('jax_theme_default')
        var ok = function (v) { return v === 'dark' || v === 'light' }
        var tema = ok(e) ? e : ok(p) ? p : 'dark'
        if (tema === 'light') {
          document.documentElement.setAttribute('data-tema', 'claro')
          document.documentElement.classList.add('light-mode')
        }
      })()
    </script>
```

(Equivalencia verificada al escribir el plan con este mismo script y este mismo módulo, en las 5 combinaciones.)

- [ ] **Step 4: `main.jsx`** — borrar las líneas 7-11 (el comentario "Apply persisted theme…" y el IIFE). El tema ya lo puso el script, antes del CSS.

- [ ] **Step 5: Verde, build y commit**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm run build >/dev/null && grep -c 'id="tema-inicial"' dist/index.html
```
Expected: todo verde (+6); `1` (Vite conserva el script en línea que no es módulo).

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/tema/aplicarTema.js frontend/src/tema/scriptTema.test.js frontend/index.html frontend/src/main.jsx
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): el tema se aplica en un script en línea antes del primer pintado" -m "Spec §5.2-§5.3. main.jsx lo hacía en un módulo diferido, después del parseo: podía parpadear. Regla: elección > último predeterminado conocido > oscuro. aplicarTema pone data-tema (tokens) y light-mode (capa vieja, hasta el PR 4). Un test corre el script extraído de index.html y exige el mismo DOM que el módulo en 5 combinaciones." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---

### Task 5: Store `useTema`, sincronización en App y BarraUsuario

**Files:**
- Create: `frontend/src/store/useTema.js`
- Delete: `frontend/src/store/useTheme.js`
- Modify: `frontend/src/App.jsx`, `frontend/src/components/BarraUsuario.jsx:3,73` (sólo el import y el hook; las clases van en la Task 9), `frontend/src/components/BarraUsuario.test.jsx` (reset del store en `beforeEach`)
- Test: `frontend/src/store/useTema.test.js`

**Interfaces:**
- Consumes: `aplicarTema`, `temaInicial`, `esTema`, `CLAVE_ELECCION`, `CLAVE_PREDETERMINADO` (Task 4); `api` de `src/api/client.js` (`baseURL: '/api'`).
- Produces: `useTema` (store zustand) con `theme: 'dark'|'light'`, `predeterminado: 'dark'|'light'|null`, `toggleTheme()`, `fijarPredeterminado(valor)`, `sincronizarPredeterminado(): Promise<void>` (hace `GET /apariencia`; **rechaza** si la petición falla).

- [ ] **Step 1: Test (RED)**

`frontend/src/store/useTema.test.js`:

```js
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../api/client', () => ({ default: { get: vi.fn() } }))

import api from '../api/client'
import { useTema } from './useTema'
import { aplicarTema } from '../tema/aplicarTema'

// Tema (spec 2026-09-14-tema-tokens §5): si el usuario eligió, gana su
// elección; si no, el theme_default del sistema, que llega por GET /apariencia.
const html = () => document.documentElement

beforeEach(() => {
  api.get.mockReset()
  localStorage.clear()
  aplicarTema('dark')
  useTema.setState({ theme: 'dark', predeterminado: null })
})

describe('useTema', () => {
  it('sin elección, el predeterminado del servidor se guarda y se aplica', async () => {
    api.get.mockResolvedValue({ data: { theme_default: 'light' } })
    await useTema.getState().sincronizarPredeterminado()
    expect(api.get).toHaveBeenCalledWith('/apariencia')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
    expect(html().classList.contains('light-mode')).toBe(true)
  })

  it('con elección guardada, un predeterminado distinto no cambia el tema', async () => {
    localStorage.setItem('jax_theme', 'dark')
    api.get.mockResolvedValue({ data: { theme_default: 'light' } })
    await useTema.getState().sincronizarPredeterminado()
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
    expect(useTema.getState().theme).toBe('dark')
    expect(html().hasAttribute('data-tema')).toBe(false)
  })

  it('toggleTheme escribe la elección y aplica los dos temas', () => {
    useTema.getState().toggleTheme()
    expect(localStorage.getItem('jax_theme')).toBe('light')
    expect(html().getAttribute('data-tema')).toBe('claro')
    useTema.getState().toggleTheme()
    expect(localStorage.getItem('jax_theme')).toBe('dark')
    expect(html().hasAttribute('data-tema')).toBe(false)
    expect(html().classList.contains('light-mode')).toBe(false)
  })

  it('un valor fuera de lista del servidor se ignora', async () => {
    api.get.mockResolvedValue({ data: { theme_default: '<b>claro</b>' } })
    await useTema.getState().sincronizarPredeterminado()
    expect(localStorage.getItem('jax_theme_default')).toBeNull()
    expect(useTema.getState().theme).toBe('dark')
  })

  it('si /apariencia falla, rechaza y se queda el último predeterminado conocido', async () => {
    localStorage.setItem('jax_theme_default', 'light')
    api.get.mockRejectedValue(new Error('red caída'))
    await expect(useTema.getState().sincronizarPredeterminado()).rejects.toThrow('red caída')
    expect(localStorage.getItem('jax_theme_default')).toBe('light')
  })
})
```

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/store/useTema.test.js 2>&1 | tail -6`
Expected: FAIL; `Failed to load url ./useTema`.

- [ ] **Step 2: `store/useTema.js`**

```js
import { create } from 'zustand'
import api from '../api/client'
import {
  CLAVE_ELECCION, CLAVE_PREDETERMINADO, esTema, temaInicial, aplicarTema,
} from '../tema/aplicarTema'

// Tema de la app (spec 2026-09-14-tema-tokens §5). Store y no useState: lo
// usan App (sincroniza el predeterminado al montar) y BarraUsuario (el
// interruptor), y dos estados locales se desalinearían. Misma interfaz que el
// useTheme de antes ({ theme, toggleTheme }) más `predeterminado`.
function predeterminadoGuardado() {
  const v = localStorage.getItem(CLAVE_PREDETERMINADO)
  return esTema(v) ? v : null
}

export const useTema = create((set, get) => ({
  theme: temaInicial(),
  predeterminado: predeterminadoGuardado(),

  // El interruptor escribe una ELECCIÓN: desde ahí el predeterminado no la pisa.
  toggleTheme: () => {
    const siguiente = get().theme === 'dark' ? 'light' : 'dark'
    localStorage.setItem(CLAVE_ELECCION, siguiente)
    aplicarTema(siguiente)
    set({ theme: siguiente })
  },

  // Guarda el último predeterminado conocido (lo lee el script de index.html
  // en la próxima carga) y lo aplica sólo si el usuario nunca eligió.
  // Un valor fuera de {'dark','light'} no se guarda ni se aplica.
  fijarPredeterminado: (valor) => {
    if (!esTema(valor)) return
    localStorage.setItem(CLAVE_PREDETERMINADO, valor)
    set({ predeterminado: valor })
    if (!esTema(localStorage.getItem(CLAVE_ELECCION)) && get().theme !== valor) {
      aplicarTema(valor)
      set({ theme: valor })
    }
  },

  // Rechaza si la petición falla: quien llama decide (App se queda con el
  // último conocido, que es la regla del spec §5.2).
  sincronizarPredeterminado: async () => {
    const { data } = await api.get('/apariencia')
    get().fijarPredeterminado(data?.theme_default)
  },
}))
```

- [ ] **Step 3: `App.jsx`**

```js
import { useTema } from './store/useTema'
```
dentro de `App`, junto al `useEffect` de `restoreSession`:

```js
  const sincronizarTema = useTema((s) => s.sincronizarPredeterminado)

  useEffect(() => {
    // Una vez por carga, también en Login y Reset (no hay sesión). Si falla, se
    // queda el último predeterminado conocido que ya aplicó el script de
    // index.html: es una preferencia visual, no una autorización (spec §5.2).
    sincronizarTema().catch(() => {})
  }, [sincronizarTema])
```

- [ ] **Step 4: BarraUsuario y su test**

`BarraUsuario.jsx`: `import { useTheme } from '../store/useTheme'` → `import { useTema } from '../store/useTema'`; `const { theme, toggleTheme } = useTheme()` → `const { theme, toggleTheme } = useTema()`.

`BarraUsuario.test.jsx`: agregar arriba `import { useTema } from '../store/useTema'` y `import { aplicarTema } from '../tema/aplicarTema'`, y en el `beforeEach` existente, después de `localStorage.clear()`:

```js
  // El store de tema es un módulo único: un test que toca el interruptor no
  // puede dejarle el tema cambiado al siguiente.
  useTema.setState({ theme: 'dark', predeterminado: null })
  aplicarTema('dark')
```

Borrar el hook viejo: `git -C /home/fruiz/worktrees/jax-platform-tema rm frontend/src/store/useTheme.js`. Verificar que no queda uso: `grep -rn "useTheme" /home/fruiz/worktrees/jax-platform-tema/frontend/src` → sin salida.

- [ ] **Step 5: Verde y commit**

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3` → todo verde (+5).

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/store/useTema.js frontend/src/store/useTema.test.js frontend/src/App.jsx frontend/src/components/BarraUsuario.jsx frontend/src/components/BarraUsuario.test.jsx
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): store useTema; App sincroniza theme_default una vez por carga" -m "Spec §5. Reemplaza al hook useTheme (estado local): ahora lo usan App y BarraUsuario. Elección > predeterminado; el predeterminado se guarda en jax_theme_default para el script de index.html; un valor fuera de lista no entra; si /apariencia falla, queda el último conocido." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```
(El `git rm` ya dejó el borrado en el índice.)

---

### Task 6: Endpoint público `GET /api/apariencia`

**Files:**
- Create: `backend/api/apariencia.py`
- Modify: `backend/main.py` (un import junto a los `from api.… import router as …` y un `app.include_router(apariencia_router)` al final de la lista)
- Test: `backend/tests/test_apariencia.py`

**Interfaces:**
- Consumes: `DEFAULT_CONFIG` de `api.admin.config_admin`; `get_pool()` de `db.connection`.
- Produces: `GET /api/apariencia` → `200 {"theme_default": "dark"|"light"}`, `Cache-Control: no-cache`, sin autenticación. Constantes `CONSULTA`, `CLAVE`, `TEMAS` (las usa el test del EXPLAIN).

- [ ] **Step 1: Tests (RED)**

`backend/tests/test_apariencia.py`:

```python
"""GET /api/apariencia (spec 2026-09-14-tema-tokens §5.2).

Público y sin parámetros: devuelve SOLO axioma_config.theme_default, validado
contra {'dark','light'}. Contra jax_memory_test (conftest.py). Cada test deja
la fila theme_default como estaba.
"""
import pytest

from api.apariencia import CLAVE, CONSULTA


async def _sql(q, args=(), fetch=False):
    from db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(q, args)
            if fetch:
                return await cur.fetchall(), [d[0] for d in cur.description]
            return None


async def _leer():
    filas, _ = await _sql("SELECT config_value FROM axioma_config WHERE config_key = %s", (CLAVE,), True)
    return filas[0][0] if filas else None


async def _poner(valor):
    await _sql(
        "INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE config_value = %s",
        (CLAVE, valor, valor),
    )


async def _borrar():
    await _sql("DELETE FROM axioma_config WHERE config_key = %s", (CLAVE,))


async def _restaurar(valor):
    if valor is None:
        await _borrar()
    else:
        await _poner(valor)


@pytest.fixture(autouse=True)
def fila_intacta(client):
    antes = client.portal.call(_leer)
    yield
    client.portal.call(_restaurar, antes)


def test_devuelve_el_valor_guardado(client):
    client.portal.call(_poner, "light")
    r = client.get("/api/apariencia")
    assert r.status_code == 200
    assert r.json() == {"theme_default": "light"}


def test_fila_ausente_da_oscuro(client):
    client.portal.call(_borrar)
    assert client.get("/api/apariencia").json() == {"theme_default": "dark"}


def test_valor_fuera_de_lista_da_oscuro(client):
    # El valor viaja a un atributo del DOM: nada fuera de la lista llega al cliente.
    for valor in ("<img src=x onerror=alert(1)>", "LIGHT", "claro", ""):
        client.portal.call(_poner, valor)
        assert client.get("/api/apariencia").json() == {"theme_default": "dark"}, valor


def test_no_devuelve_otras_claves(client):
    async def poner_smtp():
        await _sql("INSERT INTO axioma_config (config_key, config_value) VALUES (%s, %s) "
                   "ON DUPLICATE KEY UPDATE config_value = %s",
                   ("smtp.apariencia_test", "secreto-que-no-sale", "secreto-que-no-sale"))

    async def quitar_smtp():
        await _sql("DELETE FROM axioma_config WHERE config_key = %s", ("smtp.apariencia_test",))

    client.portal.call(poner_smtp)
    try:
        r = client.get("/api/apariencia")
        assert set(r.json()) == {"theme_default"}
        assert "secreto-que-no-sale" not in r.text
    finally:
        client.portal.call(quitar_smtp)


def test_responde_sin_token_y_no_valida_uno_basura(client):
    assert client.get("/api/apariencia").status_code == 200
    r = client.get("/api/apariencia", headers={"Authorization": "Bearer basura"})
    assert r.status_code == 200


def test_cache_control_no_cache(client):
    # Un cambio del admin se ve en la carga siguiente, no cuando caduque un caché.
    assert client.get("/api/apariencia").headers["cache-control"] == "no-cache"


def test_explain_usa_la_primary_sin_filesort_ni_temporary(client):
    # LAS CUATRO: EXPLAIN sobre la consulta REAL del endpoint (la misma constante).
    client.portal.call(_poner, "dark")

    async def explain():
        return await _sql("EXPLAIN " + CONSULTA, (CLAVE,), True)

    filas, columnas = client.portal.call(explain)
    plan = dict(zip(columnas, filas[0]))
    assert plan["key"] == "PRIMARY", plan
    extra = plan.get("Extra") or ""
    assert "filesort" not in extra and "temporary" not in extra, plan
```

Run: `cd /home/fruiz/worktrees/jax-platform-tema/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_apariencia.py -q 2>&1 | tail -4`
Expected: ERROR de colección, `ModuleNotFoundError: No module named 'api.apariencia'`.

- [ ] **Step 2: `backend/api/apariencia.py`**

```python
"""Apariencia pública de la instancia (spec 2026-09-14-tema-tokens §5.2).

GET /api/apariencia, SIN autenticación: el Login y el Reset lo necesitan antes
de que haya sesión, y la config de admin exige superadmin. Devuelve una sola
clave, por lista blanca (no por prefijo excluido): smtp.* vive en la misma
tabla y este endpoint no puede devolverlo aunque se agreguen claves. El valor
se valida contra una lista cerrada porque termina en un atributo del DOM.

Sin caché en el backend: una lectura por PRIMARY KEY de una tabla chica no
justifica un caché con invalidación entre procesos (LAS CUATRO: sin medición
previa, no hay caché nuevo). La prueba de carga del PR 1 lo mide.
"""
from fastapi import APIRouter, Response

from api.admin.config_admin import DEFAULT_CONFIG
from db.connection import get_pool

router = APIRouter()

CLAVE = "theme_default"
TEMAS = ("dark", "light")
# Por PRIMARY KEY (config_key). test_apariencia.py corre EXPLAIN sobre esta constante.
CONSULTA = "SELECT config_value FROM axioma_config WHERE config_key = %s"


@router.get("/api/apariencia")
async def apariencia(response: Response):
    response.headers["Cache-Control"] = "no-cache"
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CONSULTA, (CLAVE,))
            fila = await cur.fetchone()
    valor = fila[0] if fila else None
    return {"theme_default": valor if valor in TEMAS else DEFAULT_CONFIG[CLAVE]}
```

`backend/main.py`: después de `from api.motors import router as motors_router` agregar `from api.apariencia import router as apariencia_router`; después del último `app.include_router(...)` agregar `app.include_router(apariencia_router)`.

- [ ] **Step 3: Verde**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_apariencia.py -v 2>&1 | tail -10
cd /home/fruiz/worktrees/jax-platform-tema/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_no_fail_open_except.py -q 2>&1 | tail -2
cd /home/fruiz/worktrees/jax-platform-tema/backend && JAX_CI_NO_DB=1 /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest tests/test_apariencia.py -q -rs 2>&1 | tail -3
```
Expected: `7 passed`; P10 `1 passed`; sin DB, `7 skipped` (piden `client` por el fixture autouse: la regla 1 del conftest). Si el EXPLAIN no da `PRIMARY`, parar: la PK de `axioma_config` no es la que el spec supone.

- [ ] **Step 4: Mutación del test de lista blanca** — copiar `apariencia.py` al scratchpad, cambiar el `return` por `return {"theme_default": valor}`, correr: `test_valor_fuera_de_lista_da_oscuro` y `test_fila_ausente_da_oscuro` en rojo. Restaurar con `cp` desde la copia y `diff`.

- [ ] **Step 5: Commit**

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add backend/api/apariencia.py backend/main.py backend/tests/test_apariencia.py
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(api): GET /api/apariencia público, sólo theme_default y validado" -m "Spec §5.2. Login y Reset necesitan el predeterminado antes de la sesión. Lista blanca de una clave, valor dentro de {dark, light} o el de DEFAULT_CONFIG, Cache-Control: no-cache, sin caché en el backend. 7 tests contra jax_memory_test, incluido el EXPLAIN de la consulta real (PRIMARY, sin filesort ni temporary)." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---

### Task 7: AdminSettings aplica el predeterminado en este navegador al guardar

**Files:**
- Modify: `frontend/src/pages/admin/AdminSettings.jsx` (import y una línea después del `api.put`)
- Test: `frontend/src/pages/admin/AdminSettings.test.jsx` (reset del store + 1 test)

**Interfaces:**
- Consumes: `useTema.getState().fijarPredeterminado(valor)` (Task 5).

- [ ] **Step 1: Test (RED)**

En `AdminSettings.test.jsx`, agregar arriba `import { useTema } from '../../store/useTema'` e `import { aplicarTema } from '../../tema/aplicarTema'`; en el `beforeEach`, después de `localStorage.clear()`:

```js
  useTema.setState({ theme: 'dark', predeterminado: null })
  aplicarTema('dark')
```

y un test nuevo dentro del `describe`:

```js
  it('guardar el tema predeterminado lo aplica en este navegador si el usuario no eligió', async () => {
    api.get.mockResolvedValue({ data: { config: [
      { key: 'system_name', value: 'Axioma' },
      { key: 'theme_default', value: 'light' },
    ] } })
    api.put.mockResolvedValue({ data: { ok: true } })
    renderSettings()
    await guardar()
    await waitFor(() => expect(localStorage.getItem('jax_theme_default')).toBe('light'))
    expect(document.documentElement.getAttribute('data-tema')).toBe('claro')
  })
```

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/pages/admin/AdminSettings.test.jsx 2>&1 | tail -8`
Expected: FAIL; `expected null to be 'light'`.

- [ ] **Step 2: Implementación**

```js
import { useTema } from '../../store/useTema'
```
en `handleSave`, inmediatamente después de `await api.put('/admin/config', items)`:

```js
      // El nuevo predeterminado se ve en este navegador sin recargar (spec
      // §5.2.4); si el usuario eligió un tema, su elección sigue ganando.
      useTema.getState().fijarPredeterminado(config.theme_default)
```

- [ ] **Step 3: Verde y commit**

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3` → verde (+1).

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/pages/admin/AdminSettings.jsx frontend/src/pages/admin/AdminSettings.test.jsx
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): guardar theme_default en Configuración lo aplica en ese navegador" -m "Spec §5.2.4. Hasta hoy theme_default se guardaba y no hacía nada. Si el usuario no eligió tema, el cambio se ve sin recargar; el resto de los navegadores lo toman en su próxima carga por /api/apariencia." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---

### Task 8: Inter se carga de verdad (`@fontsource/inter`)

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/main.jsx`, `frontend/.impeccable/config.json` (por el comando de impeccable)

- [ ] **Step 1: node_modules propio (el symlink apunta a producción)**

`npm install` sobre el symlink escribiría en `/home/fruiz/jax-platform/frontend/node_modules`, que es de producción.

```bash
test -L /home/fruiz/worktrees/jax-platform-tema/frontend/node_modules && rm /home/fruiz/worktrees/jax-platform-tema/frontend/node_modules
test -e /home/fruiz/jax-platform/frontend/node_modules/react && echo "PRODUCCION INTACTA"
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm ci 2>&1 | tail -2
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm install --save-exact @fontsource/inter@5.3.0 2>&1 | tail -2
git -C /home/fruiz/worktrees/jax-platform-tema status --short
```
Expected: `rm` borra **el symlink**, no su destino (`PRODUCCION INTACTA`). `status` muestra sólo `package.json` y `package-lock.json` modificados; `frontend/node_modules/` ahora es un directorio y el `.gitignore` sí lo cubre. `package.json` queda con `"@fontsource/inter": "5.3.0"`, fijo como `ibm-plex-serif` (5.3.0 es la última publicada, verificado con `npm view` el 2026-09-14).

- [ ] **Step 2: Importar sólo latin y los pesos usados (400, 500, 600, 700; spec §1.2 y §9)**

En `main.jsx`, después de `import './index.css'`:

```js
// Inter en el bundle (@fontsource, OFL), como IBM Plex Serif en LogoAxioma:
// sin Google Fonts, así ninguna visita le avisa a un tercero. Sólo el subset
// latin y los pesos que usa la UI: normal 400, font-medium 500,
// font-semibold 600, font-bold 700 (font-light es de la marca, Plex Serif).
// DECISIÓN de Fernando 2026-09-14: Inter se queda aunque el hook de
// impeccable la marque como sobreusada (.impeccable/config.json).
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
import '@fontsource/inter/latin-700.css'
```

- [ ] **Step 3: Verificar `font-display: swap` y los archivos**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm run build >/dev/null && \
grep -o "font-family:Inter[^}]*" dist/assets/index-*.css | grep -c 'font-display:swap' && \
ls dist/assets | grep -E '^inter-latin-(400|500|600|700)-normal.*\.woff2$'
```
Expected: `4` y cuatro `.woff2` de Inter. Sin `font-display:swap`, parar: la fuente bloquearía el render.

- [ ] **Step 4: Registrar la DECISIÓN en impeccable**

`frontend/.impeccable/config.json` **ya** tiene una entrada `overused-font` / `inter` (creada 2026-09-14T01:17, con el motivo del 2026-09-13). Con cwd `/home/fruiz/worktrees/jax-platform-tema/frontend`, invocar el comando del plugin impeccable (skill `impeccable`, subcomando de hooks; referencia `reference/hooks.md`):

```
/impeccable hooks ignore-value overused-font inter --reason "user confirmed: Fernando 2026-09-14"
```
Luego:

```bash
node -e 'const c=require("/home/fruiz/worktrees/jax-platform-tema/frontend/.impeccable/config.json");const e=c.detector.ignoreValues.filter(v=>v.rule==="overused-font"&&v.value==="inter");console.log(e.length, e.map(v=>v.reason))'
```
Expected: `1 [ 'user confirmed: Fernando 2026-09-14' ]`. Si quedaron **dos** entradas, se borra a mano la vieja del archivo (es config compartida del repo) y se vuelve a verificar. Si el comando dejó la vieja intacta sin agregar nada, se edita su `reason` a mano con ese texto exacto.

- [ ] **Step 5: Verde y commit**

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3` → verde, sin cambio de conteo.

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/package.json frontend/package-lock.json frontend/src/main.jsx frontend/.impeccable/config.json
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): Inter se carga con @fontsource/inter (latin 400-700, swap)" -m "Spec §2.3 y §9. index.css declaraba Inter y no se cargaba: se veía Segoe UI o la sans del sistema. Sólo latin y los cuatro pesos usados, con font-display: swap. DECISIÓN de Fernando 2026-09-14 registrada en .impeccable/config.json: el hook la marca como sobreusada y se queda." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---

### Task 9: Migrar Login, Reset, barra, logotipo, AlertaError y PasswordInput

**Files:**
- Modify: `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/tema/contraste.test.js` (+2 tests), `frontend/src/components/AlertaError.jsx`, `frontend/src/components/PasswordInput.jsx`, `frontend/src/components/BarraUsuario.jsx`, `frontend/src/components/LogoAxioma.jsx` (sólo comentario), `frontend/src/pages/ResetPassword.jsx`, `frontend/src/pages/Login.jsx` (**último**, por el choque con la etapa 2)

Clases medidas en `bfab4de` con el patrón de la tabla M: Login 50 + 6 hex, ResetPassword 32, BarraUsuario 7, PasswordInput 2, AlertaError 1, LogoAxioma 0 (sus `text-oro*` ya son tokens).

**Nota sobre el spec:** §8 y §10 dicen que el `toHaveClass` de `PasswordInput.test.jsx` "se actualiza al token". Medido: ese test comprueba que `className` **pasa tal cual** (`toHaveClass('mi-clase')`), no un color. No hay nada que actualizar ahí.

- [ ] **Step 1: Tests de uso (RED)**

En `contraste.test.js`, agregar al import de `./tokens.js` `EXENTOS_TEXTO, PERMITIDOS_CRUDOS, MIGRADOS`, y al final del archivo:

```js
// Uso (spec §6.4): un archivo ya migrado no vuelve a pintar con colores crudos.
// "Ya migrado" es MIGRADOS en tokens.js; cada PR del rollout lo amplía.
const fuentes = import.meta.glob(['../**/*.{js,jsx}', '!../**/*.test.{js,jsx}'], {
  query: '?raw', import: 'default', eager: true,
})
const porRuta = Object.fromEntries(Object.entries(fuentes).map(([k, v]) => [k.replace(/^\.\.\//, ''), v]))

const PALETA = 'slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose'
const PROPIEDAD = 'bg|text|border|divide|ring|placeholder|fill|stroke|from|via|to|outline|accent|caret|decoration|shadow'
const PROHIBIDOS = [
  ['clase de paleta de Tailwind', new RegExp(`(?<![\\w-])(?:${PROPIEDAD})-(?:(?:${PALETA})-\\d{2,3}|white|black)(?:\\/\\d+)?(?![\\w-])`, 'g')],
  ['color hal-*', /(?<![\w-])(?:bg|text|border)-hal-[a-z]+/g],
  ['hex', /#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])/g],
  ['rgb()/rgba() literal', /rgba?\(\s*\d/g],
  ['prefijo dark:', /(?<![\w-])dark:[a-z]/g],
  ['color con nombre en SVG o estilo', /(?:fill|stroke)=["'](?:white|black)["']|:\s*['"](?:white|black)['"]/g],
]

describe('uso de tokens en los archivos migrados', () => {
  it('los archivos migrados pintan sólo con tokens', () => {
    expect(MIGRADOS.length).toBeGreaterThan(0) // verde sobre cero archivos no vale
    const hallazgos = []
    for (const ruta of MIGRADOS) {
      const codigo = porRuta[ruta]
      if (codigo === undefined) {
        hallazgos.push(`${ruta}: no existe (¿ruta mal escrita en MIGRADOS?)`)
        continue
      }
      for (const [que, patron] of PROHIBIDOS) {
        for (const m of codigo.match(patron) || []) {
          if (PERMITIDOS_CRUDOS.some((p) => p.archivo === ruta && p.texto === m)) continue
          hallazgos.push(`${ruta}: ${que} «${m}»`)
        }
      }
    }
    expect(hallazgos).toEqual([])
  })

  it('un token que no es primer plano de ningún par no se usa como text-* (salvo exentos)', () => {
    const frentes = new Set(PARES.map(([f]) => f))
    const hallazgos = []
    for (const ruta of MIGRADOS) {
      for (const [, token] of (porRuta[ruta] || '').matchAll(/(?<![\w-])text-([a-z0-9-]+)/g)) {
        if (!TOKENS.includes(token) || frentes.has(token)) continue
        if (EXENTOS_TEXTO[token]?.archivos.includes(ruta)) continue
        hallazgos.push(`${ruta}: text-${token}`)
      }
    }
    expect(hallazgos).toEqual([])
  })
})
```

En `tokens.js`, `MIGRADOS` pasa a:

```js
export const MIGRADOS = [
  'components/AlertaError.jsx',
  'components/BarraUsuario.jsx',
  'components/LogoAxioma.jsx',
  'components/PasswordInput.jsx',
  'pages/Login.jsx',
  'pages/ResetPassword.jsx',
]
```

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/contraste.test.js 2>&1 | grep -E '«|failed|passed' | head -20`
Expected: `1 failed | 7 passed`. Los hallazgos nombran Login (`bg-blue-600`, `#0f172a`, `fill="white"`…), ResetPassword, BarraUsuario (`text-slate-500`, `ring-blue-500`…), PasswordInput y AlertaError. LogoAxioma no aparece. El test de exentos pasa: `text-oro-oscuro` está exento en LogoAxioma. (El regex se probó al escribir el plan contra estos archivos: 58 hallazgos en Login, 34 en Reset, 7, 2, 1 y 0; ninguno en `tokens.js`.)

- [ ] **Step 2: Componentes compartidos**

`AlertaError.jsx`: `text-red-400` → `text-peligro`. El comentario de arriba pasa a `// Aviso de error de una pantalla (2026-09-14). Un solo lugar para su rol y su color (token peligro, src/tema/tokens.css).`

`PasswordInput.jsx`: `text-slate-500 hover:text-slate-300` → `text-texto-tenue hover:text-texto`. El comentario "Colores: las mismas clases slate … html.light-mode sobrescribe" pasa a `// - Colores: tokens del tema (src/tema/tokens.css), sirven en los dos temas.`

`BarraUsuario.jsx`:
- `BOTON`: `text-slate-500` → `text-texto-tenue`; `focus-visible:ring-blue-500` → `focus-visible:ring-foco`
- `BOTON_NEUTRO`: `hover:text-slate-300` → `hover:text-texto`
- el `<span>` del usuario: `text-slate-500` → `text-texto-tenue`; el correo, `text-slate-300` → `text-texto`
- separador: `bg-slate-700` → `bg-borde`
- salir: `hover:text-red-400` → `hover:text-peligro`
- comentario de cabecera: "toman los grises slate del resto de la UI, que html.light-mode ya ajusta" → "toman el color del token de texto del botón (src/tema/tokens.css)".

`LogoAxioma.jsx`: sólo el comentario "En modo claro los dorados se oscurecen … (src/index.css, html.light-mode)" → "Los dorados son tokens (oro, oro-claro, oro-oscuro en src/tema/tokens.css): en claro ya vienen oscurecidos para leerse sobre blanco."

- [ ] **Step 3: `ResetPassword.jsx`** (tabla M, reemplazos exactos; líneas de `bfab4de`, después del rebase se ubican por el texto)

| Línea(s) | Hoy | Queda |
|---|---|---|
| 25, 65 | `bg-hal-bg` | `bg-fondo` |
| 27 | `text-red-400` | `text-peligro` |
| 28 | `text-blue-400 hover:text-blue-300` | `text-info hover:underline` |
| 70 | `bg-blue-600 text-white` : `text-slate-500 hover:text-slate-300` | `bg-accion text-sobre-color` : `text-texto-tenue hover:text-texto` |
| 76 | `text-slate-200` | `text-texto` |
| 77 | `text-slate-600` | `text-texto-tenue` |
| 79 | `bg-slate-800 … border-slate-700` | `bg-superficie … border-borde` |
| 81 | `text-green-400 bg-green-900/30 border border-green-800` | `text-exito bg-exito-fondo border border-exito-borde` |
| 87, 100 | `text-slate-400` | `text-texto-suave` |
| 93, 106 | `bg-slate-900 border border-slate-600 … text-slate-200 … focus:border-blue-500` | `bg-hundido border border-borde-control … text-texto … focus:border-foco` |
| 112 | `text-red-400 bg-red-900/30 border border-red-800` | `text-peligro bg-peligro-fondo border border-peligro-borde` |
| 120 | `bg-blue-600 hover:bg-blue-500 … text-white` | `bg-accion hover:bg-accion-hover … text-sobre-color` |
| 128 | `text-slate-500 hover:text-slate-300` | `text-texto-tenue hover:text-texto` |

- [ ] **Step 4: `Login.jsx`** (último, por el choque con la etapa 2)

Los mismos reemplazos que en Reset para el selector de idioma, el `h1`, el subtítulo, la tarjeta, las etiquetas, los campos (incluido el `className` que se le pasa a `PasswordInput`), el mensaje verde de "correo enviado", las cajas de error y los botones primarios. Además:
- `← {t.backToLogin}`: `text-slate-500 hover:text-slate-300` → `text-texto-tenue hover:text-texto`. "¿Olvidaste…?": `text-slate-500 hover:text-blue-400` → `text-texto-tenue hover:text-info`.
- El ojo SVG (decorativo, sin texto) pasa de atributos de color a clases: los atributos de presentación SVG no aceptan `var()` de forma fiable (spec §7.3). El iris es el color de reposo del ojo HAL, `faceta-jax-local`:

```jsx
          <svg width="80" height="80" viewBox="0 0 80 80" aria-hidden="true">
            <circle cx="40" cy="40" r="36" className="fill-fondo stroke-superficie" strokeWidth="2" />
            <circle cx="40" cy="40" r="30" fill="none" className="stroke-faceta-jax-local" strokeWidth="1" opacity="0.3" />
            <circle cx="40" cy="40" r="16" className="fill-faceta-jax-local" opacity="0.9" />
            <circle cx="40" cy="40" r="12" className="fill-fondo" opacity="0.5" />
            <circle cx="40" cy="40" r="6" className="fill-fondo" />
            <circle cx="34" cy="34" r="2.5" className="fill-sobre-color" opacity="0.6" />
          </svg>
```
(La pupila era `#0a0f1a`, casi `fondo`; en claro queda del color del fondo. Se revisa en vivo, Task 12 Step 6.) Si la etapa 2 ya está en la rama, su bloque `avisoSesion` con `<AlertaError>` queda como está.

- [ ] **Step 5: Verde, verificación mecánica y commit**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3
cd /home/fruiz/worktrees/jax-platform-tema/frontend/src && grep -nE "(^|[^a-z-])(bg|text|border|divide|ring|placeholder|fill|stroke|from|via|to|outline|accent|caret|decoration|shadow)-((slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}|white|black)|(bg|text|border)-hal-|#[0-9a-fA-F]{3,8}([^0-9a-zA-Z]|$)|rgba?\( *[0-9]|(^|[^a-z-])dark:[a-z]|(fill|stroke)=[\"'](white|black)" components/AlertaError.jsx components/BarraUsuario.jsx components/LogoAxioma.jsx components/PasswordInput.jsx pages/Login.jsx pages/ResetPassword.jsx
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm run build >/dev/null && grep -c 'rgb(var(--accion)' dist/assets/index-*.css
```
Expected: todo verde (los tests de Login, Reset, BarraUsuario y PasswordInput incluidos); el grep **sin salida**; el build tiene reglas con `rgb(var(--accion)`.

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/tema/tokens.js frontend/src/tema/contraste.test.js frontend/src/components/AlertaError.jsx frontend/src/components/PasswordInput.jsx frontend/src/components/BarraUsuario.jsx frontend/src/components/LogoAxioma.jsx frontend/src/pages/ResetPassword.jsx frontend/src/pages/Login.jsx
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): Login, Reset, barra de usuario, logotipo, AlertaError y PasswordInput pintan con tokens" -m "Spec §7 y §8 (PR 1). Tabla de migración aplicada; el ojo del Login pasa de atributos hex a clases fill/stroke. El test de contraste agrega el escaneo de uso: un archivo de MIGRADOS no puede tener clases de paleta, hex, rgb literal, dark: ni white/black con nombre; y un token que no es primer plano de ningún par no se usa como text-* (oro-oscuro exento en el logotipo, decorativo). Visto en rojo antes de migrar." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---
### Task 10: Pisos de CI medidos, canario en rojo, PR 1 y gate por headSha

**Files:**
- Modify: `.github/workflows/policy.yml` (`numPassedTests`, `PISO_PASSED`; `JAX_CI_MIN_PASSED` sólo si la medida cambia)

- [ ] **Step 1: Rebase si hace falta (Task 1 Step 2b) y suites completas**

```bash
git -C /home/fruiz/worktrees/jax-platform-tema fetch origin
git -C /home/fruiz/worktrees/jax-platform-tema log --oneline origin/master -1
git -C /home/fruiz/worktrees/jax-platform-tema merge-base --is-ancestor origin/master HEAD && echo "AL DIA CON MASTER"
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run --reporter=default --reporter=json --outputFile=/tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad/vitest.json >/dev/null; node -e 'const r=require(process.argv[1]);console.log("vitest passed="+r.numPassedTests+" failed="+r.numFailedTests)' /tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad/vitest.json
cd /home/fruiz/worktrees/jax-platform-tema/backend && /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q -rs 2>&1 | tail -2
cd /home/fruiz/worktrees/jax-platform-tema/backend && JAX_CI_NO_DB=1 /home/fruiz/jax-platform/backend/.venv/bin/python -m pytest -q 2>&1 | tail -2
```
Expected: `AL DIA CON MASTER` (si no, rebasar primero: el PR 1 va encima de la etapa 2). vitest = **piso vigente + 21** (6 de la Task 2, 1 de la 3, 6 de la 4, 5 de la 5, 1 de la 7, 2 de la 9), 0 fallidos. Con DB = **PISO_PASSED vigente + 7**, ≤ 1 skip. Sin DB = **JAX_CI_MIN_PASSED vigente, sin cambio** (los 7 nuevos piden `client` y se saltean). Si un número no da, no se toca el piso: se busca por qué.

- [ ] **Step 2: Leer los pisos vigentes del archivo, no de este plan**

```bash
grep -n 'numPassedTests !==\|se esperaban\|PISO_PASSED = \|JAX_CI_MIN_PASSED:' /home/fruiz/worktrees/jax-platform-tema/.github/workflows/policy.yml
```

- [ ] **Step 3: Subir los pisos al número medido, con comentario**

En el job `frontend-tests`, arriba del `if`, agregar un comentario con la forma de los que ya hay (sin tildes en el bloque de `node -e`, como los vecinos):

```js
            // <vigente> -> <medido> el 2026-09-14 (tema con tokens, PR 1): +6
            // contraste.test.js (dos temas, completitud, control negativo, colorToken),
            // +2 uso de tokens en archivos migrados, +1 tailwind.test.js, +6
            // scriptTema.test.js (script de index.html = aplicarTema, 5 casos + posicion),
            // +5 useTema.test.js y +1 AdminSettings (guardar aplica el predeterminado).
            // Vistos en rojo antes de verde.
```
y reemplazar el número **en los dos lugares** (`numPassedTests !== N` y el `console.error`). En el job con DB, un comentario `# Subido de <vigente> a <medido> (2026-09-14, tema con tokens PR 1): +7 con \`client\` en test_apariencia.py (valor guardado, fila ausente, fuera de lista, sin otras claves, sin token, no-cache, EXPLAIN). Medido: <medido> / <skips>.` y `PISO_PASSED = <medido>`. `JAX_CI_MIN_PASSED` queda igual; se agrega una línea al comentario de ese bloque: `# Sin cambio (2026-09-14, tema PR 1): los 7 de test_apariencia.py piden client. Medido con JAX_CI_NO_DB=1: <n> / <total>.`

Commit:

```bash
git -C /home/fruiz/worktrees/jax-platform-tema add .github/workflows/policy.yml
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "ci: pisos exactos del PR 1 de tema (vitest y job con DB), medidos" -m "vitest <vigente> -> <medido> (+21); PISO_PASSED <vigente> -> <medido> (+7, test_apariencia.py); sin DB sin cambio. Números leídos del archivo después del rebase sobre la etapa 2." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```
(Reemplazar `<vigente>`/`<medido>` por los números reales antes de commitear.)

- [ ] **Step 4: Rama y PR**

```bash
git -C /home/fruiz/worktrees/jax-platform-tema push -u origin feat/tema-tokens
gh pr create --repo fjruizhn/jax-platform --base master --head feat/tema-tokens \
  --title "Tema claro/oscuro con tokens · PR 1: base, Login y barra de usuario" \
  --body-file /tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad/pr1-cuerpo.md
```
El cuerpo (archivo en el scratchpad, escrito con Write) dice: qué entra (spec §8, fila PR 1), las respuestas del dueño, los números de pisos, **los números de bundle, FCP/CLS y carga de la Task 11** (el PR se edita con `gh pr edit --body-file` cuando estén), que `lightModeOverrides.test.js` sigue hasta el PR 2/3 (spec §6), y el pie de atribución de Global Constraints.

- [ ] **Step 5: Canario — el job de vitest tiene que ponerse ROJO con un token roto**

Desde el mismo HEAD, en una rama descartable (el `policy.yml` corre en push a cualquier rama):

```bash
SCR=/tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad
git -C /home/fruiz/worktrees/jax-platform-tema switch -c canario/tema-contraste
cp /home/fruiz/worktrees/jax-platform-tema/frontend/src/tema/tokens.css $SCR/tokens.css.bak
sed -i '0,/--texto-tenue: 129 144 166;/s//--texto-tenue: 100 116 139;/' /home/fruiz/worktrees/jax-platform-tema/frontend/src/tema/tokens.css
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/tema/tokens.css
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "canario: texto-tenue bajo AA (NO MERGEAR)" -m "Rama descartable: el job frontend-tests tiene que ponerse rojo." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
git -C /home/fruiz/worktrees/jax-platform-tema push -u origin canario/tema-contraste
gh run list --repo fjruizhn/jax-platform --branch canario/tema-contraste --limit 1 --json databaseId,headSha,conclusion
```
Esperar a que termine. Expected: `frontend-tests` en **failure** sobre ese `headSha`, con `texto-tenue sobre fondo en oscuro: 3.75 < 4.5` en el log (`gh run view <id> --log-failed | grep texto-tenue`). Después:

```bash
git -C /home/fruiz/worktrees/jax-platform-tema switch feat/tema-tokens
git -C /home/fruiz/worktrees/jax-platform-tema branch -D canario/tema-contraste
git -C /home/fruiz/worktrees/jax-platform-tema push origin --delete canario/tema-contraste
diff $SCR/tokens.css.bak /home/fruiz/worktrees/jax-platform-tema/frontend/src/tema/tokens.css && echo "tokens.css de la rama intacto"
```

- [ ] **Step 6: Gate de CI por headSha (antes de mergear)**

```bash
gh pr checks <N> --repo fjruizhn/jax-platform
gh pr view <N> --repo fjruizhn/jax-platform --json headRefOid -q .headRefOid
git -C /home/fruiz/worktrees/jax-platform-tema ls-remote origin refs/heads/feat/tema-tokens
```
Expected: todos los checks `SUCCESS` (ni pending ni skipped inesperado) y `headRefOid` igual al sha de `ls-remote`. Si no, no se mergea. Revisión de código del PR (superpowers:requesting-code-review) antes del merge; cada hallazgo se arregla en la rama antes de mergear (sin "después").

---

### Task 11: Medidas después del PR 1 y prueba de carga de `/api/apariencia`

**Files:** ninguno del repo. Resultados en `.superpowers/sdd/tema-tokens-medidas.md` y en el cuerpo del PR; la Task 30 los lleva a DEUDA.

- [ ] **Step 1: Bundle después** — el mismo comando de la Task 1 Step 5, sobre el HEAD de la rama. Anotar JS y CSS (crudo y gzip -9) y el peso de las fuentes de Inter por separado (`ls -l dist/assets/inter-*`). Criterio (spec §9): el JS no crece más que lo que agregan `tokens.js`, `aplicarTema.js` y `useTema.js` (unos pocos KB). Si crece más, se busca qué entró antes de seguir.

- [ ] **Step 2: FCP y CLS después** — el mismo procedimiento de la Task 1 Step 6, 3 corridas por tema, mediana. Para claro con el perfil sembrado se puede sembrar ahora `jax_theme_default=light` en lugar de `jax_theme` (ejercita la ruta nueva del script). Criterio: **CLS ≤ 0,1**. Si la fuente lo empuja por encima, se agrega un `@font-face` de respaldo con `size-adjust` en `index.css` (spec §9), se vuelve a medir y se anota. Comparar FCP contra la base.

- [ ] **Step 3: Backend de la rama en un puerto aparte, contra `jax_memory_test`**

En segundo plano (run_in_background):

```bash
cd /home/fruiz/worktrees/jax-platform-tema/backend && set -a && . /etc/jax/.env && set +a && \
export JAX_DB_NAME=jax_memory_test JAX_FACET_SEAL_PATH="$(mktemp -d)/facet-cache-seal" && \
/home/fruiz/jax-platform/backend/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8091
```
`JAX_FACET_SEAL_PATH` aparte es obligatorio: el arranque estampa el sello, y el real (`/srv/jax-data/facet-cache-seal`) lo vigilan Jacobs, el REPL y LAS MANOS. Comprobar: `curl -s http://127.0.0.1:8091/api/apariencia` → `{"theme_default":...}`.

- [ ] **Step 4: Carga**

`/tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad/carga_apariencia.py` (con Write):

```python
"""Carga de GET /api/apariencia (spec tema-tokens §9). Uso: URL CONCURRENCIA N."""
import asyncio
import sys
import time

import httpx


async def main(url: str, c: int, n: int) -> None:
    latencias: list[float] = []
    errores = 0
    semaforo = asyncio.Semaphore(c)
    async with httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=c)) as cliente:
        async def una() -> None:
            nonlocal errores
            async with semaforo:
                t = time.perf_counter()
                try:
                    r = await cliente.get(url)
                    if r.status_code != 200:
                        errores += 1
                except httpx.HTTPError:  # fail-soft: script de carga, el error se CUENTA y se imprime
                    errores += 1
                latencias.append((time.perf_counter() - t) * 1000)

        t0 = time.perf_counter()
        await asyncio.gather(*(una() for _ in range(n)))
        duracion = time.perf_counter() - t0
    latencias.sort()

    def q(p: float) -> float:
        return latencias[min(len(latencias) - 1, int(p * len(latencias)))]

    print(f"c={c} n={n} errores={errores} rps={n / duracion:.0f} "
          f"p50={q(.50):.1f}ms p95={q(.95):.1f}ms p99={q(.99):.1f}ms")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
```

```bash
PY=/home/fruiz/jax-platform/backend/.venv/bin/python; S=/tmp/claude-1000/-home-fruiz/0eae1bec-6cd3-4d19-bf4b-b03e294d73a8/scratchpad/carga_apariencia.py
$PY $S http://127.0.0.1:8091/api/apariencia 1 100
$PY $S http://127.0.0.1:8091/api/apariencia 10 1000
$PY $S http://127.0.0.1:8091/api/apariencia 30 3000
$PY $S http://127.0.0.1:8091/api/apariencia 100 5000
$PY $S http://127.0.0.1:8091/api/health 30 3000
```
Peor caso: el endpoint se pide en **cada** carga de página, Login público incluido; c=100 busca dónde empieza a degradar. `/api/health` es la referencia de un endpoint sin base en el mismo proceso. Criterio: 0 errores hasta c=30, y un p95 del mismo orden que el de `GET /api/admin/config` medido el 2026-09-13 (c=30: 28,6 ms). Si el p95 en c=30 sale muy por encima, se para: antes de un GO se decide con Fernando si hace falta caché (con su invalidación en el mismo commit).

Anotar la tabla en el registro con fecha, hora y el sha. Parar el uvicorn de 8091.

---

### Task 12: Merge, deploy (backend y frontend), CSP y verificación en vivo en los dos temas

**Files:** ninguno. Requiere el **GO de Fernando** para producción.

- [ ] **Step 1: Merge y backend**

```bash
gh pr merge <N> --repo fjruizhn/jax-platform --merge
git -C /home/fruiz/jax-platform status --short | head -3
git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only
sudo -n /usr/bin/systemctl restart jax-platform.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/health
curl -s -D - http://127.0.0.1:8080/api/apariencia | grep -iE '^HTTP|cache-control|theme_default'
sudo -n /usr/bin/journalctl -u jax-platform.service --since '-3 min' --no-pager | tail -40
readlink /proc/$(systemctl show -p MainPID --value jax-platform.service)/cwd
```
Expected: el checkout de producción limpio antes del pull; `200`; `/api/apariencia` con `200`, `cache-control: no-cache` y el valor real (`dark` hoy); journal sin tracebacks; cwd = `/home/fruiz/jax-platform/backend`.

- [ ] **Step 2: Build de producción (dependencia nueva: `npm ci` primero)**

```bash
cd /home/fruiz/jax-platform/frontend && npm ci 2>&1 | tail -2 && npm run build 2>&1 | tail -3
ls /home/fruiz/jax-platform/frontend/dist/assets/index-*.js /home/fruiz/jax-platform/frontend/dist/assets/inter-latin-*-normal*.woff2
grep -c 'id="tema-inicial"' /home/fruiz/jax-platform/frontend/dist/index.html
```
Expected: 4 woff2 de Inter; `1`. Anotar el nombre del `index-*.js`. (`npm ci` en el checkout de producción es parte del deploy: `@fontsource/inter` es nueva.)

- [ ] **Step 3: Backup en la VM, verificado**

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
ssh -p 58291 fruiz@172.16.20.11 "sudo cp -a /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-tema-pr1-$STAMP && sudo diff -rq /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-tema-pr1-$STAMP && echo BACKUP-IDENTICO"
```
Expected: `BACKUP-IDENTICO`. Sin eso no se sigue.

- [ ] **Step 4: rsync en dos saltos, con `--exclude .user.ini` en los dos**

```bash
rsync -a --delete --exclude .user.ini -e "ssh -p 58291" /home/fruiz/jax-platform/frontend/dist/ fruiz@172.16.20.11:/tmp/axioma-deploy/
ssh -p 58291 fruiz@172.16.20.11 "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"
curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
md5sum /home/fruiz/jax-platform/frontend/dist/assets/index-*.js; curl -s https://axioma-ia.io/assets/$(curl -s https://axioma-ia.io/ | grep -o 'index-[A-Za-z0-9_-]*\.js' | head -1) | md5sum
```
Expected: el `index-*.js` servido es el recién construido y el md5 local es igual al servido.

- [ ] **Step 5: CSP y rutas nuevas en vivo**

```bash
curl -s -D - -o /dev/null https://axioma-ia.io/login | grep -i 'content-security-policy' || echo "SIN CSP"
curl -s https://axioma-ia.io/login | grep -c 'id="tema-inicial"'
curl -s -D - https://axioma-ia.io/api/apariencia | grep -iE '^HTTP|cache-control|theme_default'
```
Expected: `SIN CSP` (como se midió el 2026-09-14); `1`; `200` con `{"theme_default":"dark"}`. **Si aparece una CSP**, parar. Calcular el hash del script exacto (`node -e` con `crypto.createHash('sha256')` sobre el texto entre `<script id="tema-inicial">` y `</script>` de `dist/index.html`, en base64) y llevárselo a Fernando: se agrega `'sha256-…'` en el nginx de atemai, **nunca** `'unsafe-inline'`. Es config fuera del repo; la decide él.

- [ ] **Step 6: Verificación en vivo, con Fernando, en los dos temas**

En Chrome, ventana de incógnito (sin `localStorage`):
1. `https://axioma-ia.io/login` en oscuro, sin parpadeo. Consola: `document.documentElement.dataset.tema` → `undefined`; `localStorage.jax_theme_default` → `"dark"` (lo guardó la sincronización). Fuente: en DevTools, Computed → `font-family` del `<h1>` y "Rendered Fonts" dice **Inter**. El ojo del Login se ve como antes.
2. Fernando entra, abre Admin → Configuración, pone Tema = Claro y Guarda: su navegador pasa a claro sin recargar (si nunca tocó el interruptor; si lo tocó, su elección gana y eso **también** se anota como correcto).
3. Otra ventana de incógnito nueva: primera visita, oscuro y en seguida claro (el único parpadeo previsto, spec §5.2.3); recargar → **claro desde el primer pintado**.
4. En claro y en oscuro, pantalla por pantalla del grupo: Login, "¿Olvidaste tu contraseña?" (con y sin error), Reset con token inválido, la barra de usuario (los cuatro íconos, hover y foco con Tab), el logotipo. Todo legible; los campos con borde visible.
5. Chequeo mecánico en la consola de cada pantalla del grupo:
   ```js
   [...document.querySelectorAll('[class]')].filter(e => /(?:^|\s)(?:[a-z-]+:)*(?:bg|text|border|ring)-(?:slate|gray|red|green|blue|purple|orange|amber|yellow|emerald)-\d/.test(e.getAttribute('class'))).map(e => e.getAttribute('class'))
   ```
   Expected en Login y Reset: `[]`. En el panel se verán las clases de pantallas que migran en PR 2-4: es lo esperado.
6. El interruptor de la barra: pasa a claro y vuelve; recargar conserva la elección.
7. Volver `theme_default` al valor que Fernando quiera dejar (hoy `dark`).

Anotar en el registro la hora, el bundle servido, el backup y el resultado de cada punto. **PR 1 cerrado.**

---
## Procedimiento C — cierre de un PR de pantallas (lo usan las Tasks 19, 25 y 28)

Parámetros: `<RAMA>`, `<N>` (número del PR), `<GRUPO>` (lista de archivos), `<DELTA>` (tests de vitest agregados menos borrados), `<ETIQUETA>` (`pr2`, `pr3`, `pr4`).

1. **Suites y pisos.** Las tres suites como en la Task 10 Step 1, con el HEAD al día con `origin/master` (`merge-base --is-ancestor`). vitest = piso vigente + `<DELTA>`, 0 fallidos; los dos pisos de pytest sin cambio (estos PRs no tocan backend), medidos igual. Se sube `numPassedTests` en los dos lugares con comentario `// <vigente> -> <medido> el <fecha> (tema con tokens, <ETIQUETA>): <qué tests>. Vistos en rojo antes de verde.` y se commitea `.github/workflows/policy.yml` solo, con los trailers.
2. **Bundle y FCP/CLS** como en la Task 1 Steps 5-6 (3 corridas por tema, mediana) y al registro. Criterio: el JS no crece más que el código agregado; el CSS **baja** a medida que la capa vieja se borra; CLS ≤ 0,1.
3. **PR:** `git -C /home/fruiz/worktrees/jax-platform-tema push -u origin <RAMA>` y `gh pr create --repo fjruizhn/jax-platform --base master --head <RAMA> --title "Tema con tokens · <ETIQUETA>: …" --body-file <scratchpad>/<ETIQUETA>-cuerpo.md`. El cuerpo lista `<GRUPO>` con su conteo de clases antes/después (0), los números del punto 2 y el pie de atribución.
4. **Gate por headSha:** `gh pr checks <N>`, `gh pr view <N> --json headRefOid -q .headRefOid` y `git -C /home/fruiz/worktrees/jax-platform-tema ls-remote origin refs/heads/<RAMA>`: todo `SUCCESS` y los dos shas iguales. Revisión de código y cada hallazgo arreglado antes del merge.
5. **Merge y deploy del frontend** (con el GO de Fernando): `gh pr merge <N> --merge`; `git -C /home/fruiz/jax-platform switch master && git -C /home/fruiz/jax-platform pull --ff-only`; `cd /home/fruiz/jax-platform/frontend && npm ci && npm run build`; backup `ssh -p 58291 fruiz@172.16.20.11 "sudo cp -a /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-tema-<ETIQUETA>-$STAMP && sudo diff -rq /www/wwwroot/axioma-ia.io /www/wwwroot/axioma-ia.io.backup-pre-tema-<ETIQUETA>-$STAMP && echo BACKUP-IDENTICO"`; `rsync -a --delete --exclude .user.ini -e "ssh -p 58291" /home/fruiz/jax-platform/frontend/dist/ fruiz@172.16.20.11:/tmp/axioma-deploy/`; `ssh -p 58291 fruiz@172.16.20.11 "sudo rsync -a --delete --exclude .user.ini --chown=www:www /tmp/axioma-deploy/ /www/wwwroot/axioma-ia.io/"`; md5 del `index-*.js` local igual al servido; `curl -s -D - -o /dev/null https://axioma-ia.io/login | grep -i content-security-policy || echo "SIN CSP"`. No hay deploy de backend (el servicio no cambia; `/api/health` 200 como control).
6. **En vivo, con Fernando, en los dos temas:** cada pantalla de `<GRUPO>` en claro y en oscuro (interruptor de la barra), con un estado de error y uno vacío cuando la pantalla los tenga, hover y foco con Tab en controles. En la consola de cada una, el chequeo mecánico de la Task 12 Step 6.5 → `[]` en las pantallas del grupo. Una ventana de incógnito con `theme_default` en claro para ver que no hay parpadeo en recarga. Anotar todo en el registro.

---

# PR 2 · Admin (Tasks 13-19)

Grupo y clases medidas en `bfab4de` (patrón de la tabla M, variantes `hover:`/`focus:` incluidas): `pages/admin/AdminMotors.jsx` 73 · `AdminFacetsModels.jsx` 56 · `AdminUsers.jsx` 53 · `AdminModelCatalog.jsx` 45 · `AdminSmtp.jsx` 44 · `AdminRepository.jsx` 34 · `AdminSettings.jsx` 34 · `AdminCosts.jsx` 31 (+4 hex) · `AdminFacetBindings.jsx` 26 (+2 hex) · `AdminDashboard.jsx` 25 · `components/admin/AdminSidebar.jsx` 14 · `pages/Admin.jsx` 2. **Total 437 + 6 hex.** (El spec §1.2 da conteos algo menores porque no contó los prefijos `hover:`/`focus:`; los de acá son los que ve el grep de la tabla M.) Las etapas 3-5 de usuarios pueden haber cambiado estos archivos: al empezar se vuelve a contar y se anota.

### Task 13: Rama del PR 2 y `tokenDeFaceta`

**Files:**
- Modify: `frontend/src/tema/tokens.js`, `frontend/src/tema/contraste.test.js` (+1)

**Interfaces:**
- Produces: `tokenDeFaceta(clave: string): string`. Clave de faceta del backend (`jax_local`, `hyde`…) → `'faceta-jax-local'`, `'faceta-hyde'`…; una clave desconocida → `'texto-suave'`. La usan AdminCosts, AdminFacetBindings (Task 17) y el store (Task 20).

- [ ] **Step 1: Rama desde master con el PR 1 adentro**

```bash
git -C /home/fruiz/worktrees/jax-platform-tema fetch origin
git -C /home/fruiz/worktrees/jax-platform-tema switch -c feat/tema-tokens-admin origin/master
git -C /home/fruiz/worktrees/jax-platform-tema log --oneline -1
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm ci 2>&1 | tail -1 && npx vitest run 2>&1 | tail -3
```
Recontar el grupo con el grep de la tabla M y `wc -l` por archivo; anotar en el registro.

- [ ] **Step 2: Test (RED)** — en `contraste.test.js`, agregar `tokenDeFaceta` al import y dentro de `describe('tokens de color')`:

```js
  it('tokenDeFaceta traduce la clave del backend y cae en texto-suave si no la conoce', () => {
    expect(tokenDeFaceta('jax_local')).toBe('faceta-jax-local')
    expect(tokenDeFaceta('hyde')).toBe('faceta-hyde')
    expect(tokenDeFaceta('claude')).toBe('texto-suave')
    expect(tokenDeFaceta(undefined)).toBe('texto-suave')
  })
```
Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/contraste.test.js 2>&1 | tail -5` → FAIL: `tokenDeFaceta is not a function` (import undefined).

- [ ] **Step 3: Implementación** (en `tokens.js`, debajo de `colorToken`)

```js
// Clave de faceta del backend (jax_local, hyde, ...) -> su token de identidad.
// DALL·E no es una faceta: se pide 'faceta-imagen' directo. Una clave que el
// tema no conoce cae en el mismo respaldo que colorToken.
export function tokenDeFaceta(clave) {
  const token = `faceta-${String(clave).replaceAll('_', '-')}`
  return TOKENS.includes(token) ? token : RESPALDO
}
```

- [ ] **Step 4: Verde y commit**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/tema/tokens.js frontend/src/tema/contraste.test.js
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): tokenDeFaceta traduce la clave de faceta a su token" -m "Spec §7.3. Admin y chat pintan facetas por clave; con esto no dependen de que el store guarde hex." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```

---

### Task 14: Constantes de AdminSmtp, AdminSmtp, AdminSettings, Admin.jsx y AdminSidebar

**Files:** `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/pages/admin/AdminSmtp.jsx`, `frontend/src/pages/admin/AdminSettings.jsx`, `frontend/src/pages/Admin.jsx`, `frontend/src/components/admin/AdminSidebar.jsx`

- [ ] **Step 1: RED** — agregar a `MIGRADOS`: `'pages/admin/AdminSmtp.jsx'`, `'pages/admin/AdminSettings.jsx'`, `'pages/Admin.jsx'`, `'components/admin/AdminSidebar.jsx'`. Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/tema/contraste.test.js 2>&1 | grep -c '«'` → un número > 0 (≈ 94: las clases del grupo), y el test `los archivos migrados pintan sólo con tokens` en FAIL nombrando esos cuatro archivos.

- [ ] **Step 2: Constantes compartidas primero** (spec §7.2; arrastran la mayoría de los usos de AdminSmtp):

```js
const INPUT = 'w-full bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto placeholder-texto-tenue focus:outline-none focus:border-foco'
const LABEL = 'block text-xs text-texto-suave mb-1 font-semibold uppercase tracking-wider'
const BOTON = 'px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50'
```
(`INPUT` pasa de `bg-slate-800` a `bg-hundido`: el campo va dentro de una tarjeta `bg-superficie`, y `hundido` es el token de campo. `BOTON` no tenía color.)

- [ ] **Step 3: El resto de las clases de los cuatro archivos con la tabla M.** En AdminSettings, los `<input>`/`<select>` usan `bg-slate-800 border-slate-600 text-slate-200 focus:border-purple-500` → `bg-hundido border-borde-control text-texto focus:border-foco`; el botón Guardar `bg-purple-600 hover:bg-purple-700 text-white` → `bg-acento hover:bg-acento-hover text-sobre-color`; el título `text-slate-100` → `text-texto-fuerte`. En AdminSidebar, el ítem activo con `bg-purple-900/40 text-purple-300` → `bg-acento-fondo text-acento-texto` (par declarado); los inactivos con `hover:bg-slate-800` → `hover:bg-superficie hover:text-texto`.

- [ ] **Step 4: Verde, grep y commit**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3
cd /home/fruiz/worktrees/jax-platform-tema/frontend/src && grep -nE "(^|[^a-z-])(bg|text|border|divide|ring|placeholder|fill|stroke|from|via|to|outline|accent|caret|decoration|shadow)-((slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}|white|black)|(bg|text|border)-hal-|#[0-9a-fA-F]{3,8}([^0-9a-zA-Z]|$)|rgba?\( *[0-9]|(^|[^a-z-])dark:[a-z]|(fill|stroke)=[\"'](white|black)" pages/admin/AdminSmtp.jsx pages/admin/AdminSettings.jsx pages/Admin.jsx components/admin/AdminSidebar.jsx
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/tema/tokens.js frontend/src/pages/admin/AdminSmtp.jsx frontend/src/pages/admin/AdminSettings.jsx frontend/src/pages/Admin.jsx frontend/src/components/admin/AdminSidebar.jsx
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): AdminSmtp (INPUT/LABEL/BOTON), Configuración, marco de Admin y menú lateral con tokens" -m "Spec §7.2 (PR 2). Las constantes compartidas primero; el resto con la tabla de migración. Los cuatro archivos entran a MIGRADOS." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```
Expected: vitest verde (AdminSmtp.test y AdminSettings.test incluidos); grep sin salida.

---

### Task 15: AdminUsers y AdminMotors

**Files:** `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/pages/admin/AdminUsers.jsx` (53), `frontend/src/pages/admin/AdminMotors.jsx` (73)

- [ ] **Step 1: RED** — agregar `'pages/admin/AdminUsers.jsx'` y `'pages/admin/AdminMotors.jsx'` a `MIGRADOS`; correr `contraste.test.js` → FAIL nombrando los dos.
- [ ] **Step 2: Tabla M.** Casos del grupo: las filas de tabla `bg-slate-900/50 hover:bg-slate-800/30` → `bg-hundido hover:bg-superficie` (tienen texto: nada translúcido); `divide-slate-800/50` → `divide-borde`; modales `bg-black/60` → `bg-fondo/70`; insignias de estado con texto (`text-green-400 bg-green-900/40`, `text-red-300 bg-red-900/40`, `text-orange-400 bg-orange-900/40`, `text-yellow-400`) → `text-exito bg-exito-fondo`, `text-peligro bg-peligro-fondo`, `text-aviso bg-aviso-fondo`; botones destructivos `bg-red-600 text-white hover:bg-red-700` → `bg-peligro-solido text-sobre-color hover:bg-peligro-solido-hover`; los botones de acción de fila con `hover:bg-red-900/60`, `hover:bg-blue-900/60`, `hover:bg-green-800/50`, `hover:bg-purple-800/50`, `hover:bg-orange-900/60` → `hover:bg-peligro-fondo`, `hover:bg-info-fondo`, `hover:bg-exito-fondo`, `hover:bg-acento-fondo`, `hover:bg-aviso-fondo` (con el texto de estado del mismo color: son pares declarados). Los `PasswordInput` de AdminUsers ya pintan con tokens; su `className` usa `INPUT`-equivalente de la tabla.
- [ ] **Step 3: Verde + grep** (el comando de la Task 14 Step 4 sobre estos dos archivos) → vitest verde y grep sin salida.
- [ ] **Step 4: Commit** con `git add` de los tres archivos y el mensaje `feat(tema): Usuarios y Motores de Admin con tokens` / cuerpo `PR 2. Tabla de migración; filas de tabla y modales sin fondos translúcidos bajo texto. Entran a MIGRADOS.` y los trailers.

---

### Task 16: AdminFacetsModels y AdminModelCatalog

**Files:** `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/pages/admin/AdminFacetsModels.jsx` (56), `frontend/src/pages/admin/AdminModelCatalog.jsx` (45)

- [ ] **Step 1: RED** — los dos a `MIGRADOS`; `contraste.test.js` → FAIL nombrándolos.
- [ ] **Step 2: Tabla M.** Casos: `bg-slate-950` → `bg-fondo`; `bg-slate-900/60`, `/80` con texto → `bg-hundido`; `border-purple-800/50` → `border-acento/40`; `bg-purple-950/20` → `bg-acento-fondo`; `bg-orange-950/10` → `bg-aviso-fondo`; `bg-red-950/30`, `bg-green-950/30` → `bg-peligro-fondo`, `bg-exito-fondo`; `bg-green-400`/`bg-red-400` (puntos sin texto) → `bg-exito`/`bg-peligro`; `bg-blue-900/40` con texto → `bg-info-fondo`.
- [ ] **Step 3: Verde + grep** sobre los dos archivos → verde y sin salida.
- [ ] **Step 4: Commit** (`feat(tema): Facetas y modelos, y Catálogo de modelos de Admin con tokens`, trailers).

---

### Task 17: AdminRepository, AdminDashboard, AdminCosts y AdminFacetBindings

**Files:** `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/pages/admin/AdminRepository.jsx` (34), `frontend/src/pages/admin/AdminDashboard.jsx` (25), `frontend/src/pages/admin/AdminCosts.jsx` (31 + 4 hex), `frontend/src/pages/admin/AdminFacetBindings.jsx` (26 + 2 hex)

**Interfaces:** consume `colorToken`, `tokenDeFaceta` (Tasks 2 y 13). AdminCosts y AdminFacetBindings **dejan de importar** `FACET_COLORS` (el store lo cambia en la Task 20).

- [ ] **Step 1: RED** — los cuatro a `MIGRADOS`; FAIL nombrándolos (incluye los `#94a3b8`).
- [ ] **Step 2: AdminCosts** — cambiar el import `import { FACET_COLORS } from '../../store/useJaxStore'` por `import { colorToken, tokenDeFaceta } from '../../tema/tokens'`, y:
  - barra del gráfico (sin texto): `backgroundColor: FACET_COLORS[f] || '#94a3b8'` → `backgroundColor: colorToken(tokenDeFaceta(f))`
  - punto de la leyenda: `style={{ backgroundColor: colorToken(tokenDeFaceta(f)) }}`
  - insignia con texto (línea ~102). Hoy es texto del color de la faceta sobre un tinte del 12 %: el test sólo mide fondos opacos, así que el tinte se reemplaza por la superficie opaca y un borde del color (decorativo):
    ```jsx
    <span className="text-xs font-semibold px-2 py-0.5 rounded border bg-superficie"
      style={{ color: colorToken(tokenDeFaceta(r.facet)), borderColor: colorToken(tokenDeFaceta(r.facet), 0.4) }}>{r.facet}</span>
    ```
  - filas `bg-slate-900/50 hover:bg-slate-800/30` → `bg-hundido hover:bg-superficie`. Ojo: sobre `hundido` el par es `faceta-*`/`hundido`, pero la insignia pone `bg-superficie`, así que el texto se mide contra `superficie` (par declarado).
- [ ] **Step 3: AdminFacetBindings** — mismo cambio de import; la insignia de la línea ~74:
  ```jsx
  <span className="text-xs font-semibold px-2 py-0.5 rounded border bg-superficie"
    style={{ color: colorToken(tokenDeFaceta(b.facet_key)), borderColor: colorToken(tokenDeFaceta(b.facet_key), 0.4) }}>
  ```
  y el resto de las clases con la tabla M. AdminRepository y AdminDashboard: sólo tabla M.
- [ ] **Step 4: Verde + grep** sobre los cuatro → verde, sin salida; además `grep -n "FACET_COLORS" frontend/src/pages/admin/*.jsx` → sin salida.
- [ ] **Step 5: Commit** (`feat(tema): Repositorio, Tablero, Costos y Asignación de facetas de Admin con tokens` / cuerpo que explique el reemplazo del tinte bajo texto por superficie opaca + borde, trailers).

---

### Task 18: Borrar de la capa vieja lo que Admin ya no usa

**Files:** `frontend/src/index.css`

`lightModeOverrides.test.js` **se queda**: el chat (PR 3) todavía usa rojos y verdes tintados (`text-red-400`, `bg-red-900`, `text-green-400`…). Se borran sólo los overrides de clases que ya **no aparecen** en ningún fuente.

- [ ] **Step 1: Listar los overrides sin uso** (el script se probó al escribir el plan: sobre `bfab4de` encuentra 51 overrides y 1 sin uso, `hover:text-slate-400`)

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && node -e '
const fs=require("fs"),path=require("path");
const css=fs.readFileSync("src/index.css","utf8");
const src=[];(function w(d){for(const f of fs.readdirSync(d)){const p=path.join(d,f);if(fs.statSync(p).isDirectory())w(p);else if(/\.jsx?$/.test(f)&&!/\.test\./.test(f))src.push(fs.readFileSync(p,"utf8"))}})("src");
const todo=src.join("\n");let n=0,sin=[];
for(const m of css.matchAll(/html\.light-mode \.((?:[^\s{:\\]|\\.)+)(?::hover|::placeholder|:focus)?\s*\{/g)){n++;const clase=m[1].replace(/\\(.)/g,"$1");if(!todo.includes(clase))sin.push(clase)}
console.log("overrides",n,"sin uso",sin)'
```
- [ ] **Step 2:** borrar de `index.css` una línea por cada clase listada (y el comentario de un bloque que quede vacío). Volver a correr el Step 1 → `sin uso []`.
- [ ] **Step 3:** `npx vitest run` → verde (`lightModeOverrides.test.js` incluido: las clases que siguen en uso conservan su override). Commit (`refactor(tema): la capa html.light-mode pierde los overrides que Admin ya no usa`, con la cuenta antes/después en el cuerpo, trailers).

---

### Task 19: Cierre del PR 2

Procedimiento C con `<RAMA>` = `feat/tema-tokens-admin`, `<GRUPO>` = los 12 archivos de arriba, `<DELTA>` = **+1** (`tokenDeFaceta`; los tests de uso no cambian de conteo porque recorren `MIGRADOS` dentro de un solo `it`), `<ETIQUETA>` = `pr2`. Pantallas a recorrer en vivo: Admin → Tablero, Usuarios (alta, error, modal), Motores, Facetas y modelos, Catálogo, Asignación de facetas, Costos (gráfico y tabla), Repositorio, Correo (SMTP, incluido el aviso de configuración corrupta y el diálogo de destinatario), Configuración.

---
# PR 3 · Chat y pipelines (Tasks 20-25)

Grupo y clases medidas en `bfab4de`: `components/BottomBar/PipelineModal.jsx` 50 (+4 hex, `rgba`) · `RightPanel/RightPanel.jsx` 25 (+1 hex) · `BottomBar/BottomBar.jsx` 24 (+10 hex) · `BottomBar/KillSwitch.jsx` 18 · `RightPanel/AuditLog.jsx` 18 · `LeftPanel/LeftPanel.jsx` 16 · `Notifications/Toast.jsx` 12 · `RightPanel/StepCard.jsx` 12 · `LeftPanel/FacetCard.jsx` 10 (+1 hex) · `chat/FileAttachment.jsx` 7 · `CenterPanel/Message.jsx` 6 (+5 hex) · `chat/AttachButton.jsx` 5 · `CenterPanel/CenterPanel.jsx` 1 (`bg-hal-bg`) · `HalEye/HalEye.jsx` 0 clases (+4 hex, `fill="white"`) · `HalEye/HalEye.css` (+1 hex) · `store/useJaxStore.js` (+14 hex en `FACET_COLORS` y `getEyeState`). **Total 204 clases + 40 hex.**

**Hallazgo que el spec no cubre (resuelto con su propia regla §7.1):** hay fondos del color de la faceta **con texto encima**: el botón Enviar de BottomBar (texto blanco sobre la faceta: con `faceta-jax-local` oscuro `#60a5fa` da 2,54:1), la faceta elegida en BottomBar y el tilde de PipelineModal. Y hay tintes translúcidos bajo texto: FacetCard activa, el avatar de Message, las etiquetas de PipelineModal. Solución: (a) el texto sobre un fondo de faceta pasa a `text-fondo`, y se declaran **9 pares nuevos** `fondo`/`faceta-*` (la matriz va de 88 a **97**). Pasan en los dos temas: el contraste es simétrico y cada `faceta-*`/`fondo` ya pasa (mínimo 5,98 en oscuro y 4,80 en claro, spec §3.3). (b) Los tintes bajo texto pasan a `bg-superficie` opaca con un borde del color de la faceta (decorativo). Es un cambio visible chico, anotado en el cuerpo del PR.

### Task 20: Colores de faceta como tokens — store, ojo HAL, FacetCard y Message

**Files:**
- Modify: `frontend/src/tema/tokens.js` (`PARES` +9, `MIGRADOS`), `frontend/src/tema/contraste.test.js` (88 → 97 en los dos tests de pares), `frontend/src/store/useJaxStore.js:70-83, 497-519`, `frontend/src/components/HalEye/HalEye.jsx`, `frontend/src/components/HalEye/HalEye.css:9`, `frontend/src/components/LeftPanel/FacetCard.jsx`, `frontend/src/components/CenterPanel/Message.jsx`
- Test: `frontend/src/store/useJaxStore.eyeState.test.js` (+1)

**Interfaces:**
- Consumes: `tokenDeFaceta`, `colorToken`, `TOKENS` (Tasks 2 y 13).
- Produces: `FACET_TOKENS: { [clave]: token }` (reemplaza a `FACET_COLORS`); cada faceta del store tiene `token` (antes `color`); `getEyeState(...)` devuelve `{ token, animation, label }` (antes `color`). BottomBar y PipelineModal leen `token` en la Task 21. Mientras tanto caen en su respaldo gris, porque ya tienen `|| '#94a3b8'`: es un estado intermedio de la rama y no se mergea así.

- [ ] **Step 0: Rama del PR 3 desde master con el PR 2 adentro**

```bash
git -C /home/fruiz/worktrees/jax-platform-tema fetch origin
git -C /home/fruiz/worktrees/jax-platform-tema switch -c feat/tema-tokens-chat origin/master
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npm ci 2>&1 | tail -1 && npx vitest run 2>&1 | tail -3
```
Recontar el grupo con el grep de la tabla M y anotarlo en el registro.

- [ ] **Step 1: Tests (RED)**

En `useJaxStore.eyeState.test.js`, agregar `import { TOKENS } from '../tema/tokens'` y:

```js
it('cada estado del ojo devuelve un token del tema, no un hex', () => {
  const pensando = { hyde: { status: 'thinking', token: 'faceta-hyde' } }
  const casos = [
    getEyeState({}, {}, true, true),                                  // kill switch
    getEyeState({}, {}, true, false, true),                           // DALL·E
    getEyeState(pensando, {}, true, false),                           // faceta pensando
    getEyeState({}, {}, false, false),                                // LAS MANOS caído
    getEyeState({}, { p: { status: 'waiting_gate' } }, true, false),  // gate
    getEyeState({}, { p: { status: 'running' } }, true, false),       // Jacobs
    getEyeState({}, {}, true, false),                                 // reposo
  ]
  expect(casos.map((e) => e.token)).toEqual([
    'peligro', 'faceta-imagen', 'faceta-hyde', 'texto-tenue', 'aviso', 'faceta-jacobs', 'faceta-jax-local',
  ])
  for (const e of casos) expect(TOKENS).toContain(e.token)
})
```
(Si el import de `getEyeState` del archivo tiene otra forma, se usa la existente.) En `contraste.test.js`, los dos `it('los 88 pares…')` pasan a `'los 97 pares…'` y `toHaveLength(97)`. En `tokens.js`, agregar a `MIGRADOS`: `'store/useJaxStore.js'`, `'components/HalEye/HalEye.jsx'`, `'components/LeftPanel/FacetCard.jsx'`, `'components/CenterPanel/Message.jsx'`.

Run: `cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run src/store/useJaxStore.eyeState.test.js src/tema/contraste.test.js 2>&1 | tail -8`
Expected: FAIL. Los tokens del ojo son `undefined` (hoy devuelve `color`); `PARES` tiene 88; el escaneo de uso lista los hex del store, HalEye, FacetCard y Message.

- [ ] **Step 2: Pares nuevos** — en `tokens.js`, al final de `PARES`:

```js
  // Texto oscuro sobre un fondo del color de la faceta (Enviar y faceta
  // elegida en BottomBar, tilde de PipelineModal). PR 3 del rollout.
  ...FACETAS.map((f) => ['fondo', f, AA_TEXTO]),
```

- [ ] **Step 3: Store** (`useJaxStore.js`)

```js
import { tokenDeFaceta } from '../tema/tokens'

// Token de identidad de cada faceta (spec 2026-09-14-tema-tokens §7.3): el
// store guarda el NOMBRE del token, no un hex; quien pinta usa colorToken().
export const FACET_TOKENS = Object.fromEntries(
  ['jax_local', 'jekyll', 'hyde', 'hipatia', 'thot', 'kimi', 'ada', 'jacobs'].map((n) => [n, tokenDeFaceta(n)]),
)

const DEFAULT_FACETS = Object.keys(FACET_TOKENS).reduce((acc, name) => {
  acc[name] = { name, status: 'idle', last_message: '', token: FACET_TOKENS[name] }
```
(el resto del `reduce` sigue igual). `getEyeState`, cada `return`:

```js
  if (killSwitchActive) return { token: 'peligro', animation: 'none', label: 'KILL SWITCH' }
  if (generatingImage) return { token: 'faceta-imagen', animation: 'pulse-fast', label: 'DALL-E 3' }
  …
    return { token: f.token, animation: anim, label: name }
  if (!lasManos) return { token: 'texto-tenue', animation: 'none', label: 'LAS MANOS DOWN' }
  if (hasGate) return { token: 'aviso', animation: 'blink', label: 'GATE' }
  if (hasRunning) return { token: 'faceta-jacobs', animation: 'pulse-slow', label: 'Jacobs' }
  return { token: 'faceta-jax-local', animation: 'pulse-slow', label: idleLabel }
```
Verificar que no queda lector del nombre viejo: `grep -rn "FACET_COLORS\|\.color\b" /home/fruiz/worktrees/jax-platform-tema/frontend/src --include=*.js --include=*.jsx | grep -v test` → sólo BottomBar y PipelineModal (Task 21).

- [ ] **Step 4: HalEye** — los atributos de presentación SVG no aceptan `var()` de forma fiable (spec §7.3): el color va por `style` o por clase.

```jsx
import { colorToken } from '../../tema/tokens'
…
  const color = colorToken(eye.token)
…
    <div className="hal-eye-container" style={{ '--eye-color': color }}>
…
          <circle cx={r} cy={r} r={outerR} className="fill-fondo stroke-superficie" strokeWidth="3" />
          <circle cx={r} cy={r} r={outerR * 0.88} fill="none" style={{ stroke: color }} strokeWidth="1.5" opacity="0.3" className="hal-glow-ring" />
          <circle cx={r} cy={r} r={outerR * 0.72} fill="none" style={{ stroke: color }} strokeWidth="1" opacity="0.2" className="hal-glow-ring" />
          <circle cx={r} cy={r} r={irisR} style={{ fill: color }} opacity="0.9" className="hal-iris" />
          <circle cx={r} cy={r} r={irisR * 0.75} className="fill-fondo" opacity="0.5" />
          <circle cx={r} cy={r} r={pupilR} className="fill-fondo" />
          <circle cx={r - irisR * 0.25} cy={r - irisR * 0.25} r={pupilR * 0.35} className="fill-sobre-color" opacity="0.6" />
          {eye.animation === 'blink' && (
            <circle cx={r} cy={r} r={outerR * 0.97} fill="none" style={{ stroke: color }} strokeWidth="3" opacity="0.8" className="hal-anim-blink" />
          )}
…
      <div className="absolute bottom-0 text-xs font-mono opacity-40" style={{ color }}>
```
Los comentarios de cada círculo se conservan. `HalEye.css:9`: `var(--eye-color, #3b82f6)` → `var(--eye-color, rgb(var(--faceta-jax-local)))`.

- [ ] **Step 5: FacetCard**

```jsx
import { colorToken } from '../../tema/tokens'
…
    <div
      className={`px-3 py-2 rounded-lg border transition-all cursor-default bg-superficie ${
        active ? '' : 'border-borde hover:border-borde-control'
      }`}
      style={active ? {
        borderColor: colorToken(facet.token),
        boxShadow: `0 0 8px ${colorToken(facet.token, 0.25)}`,
      } : {}}
    >
      …
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${STATUS_DOT[facet.status] || 'bg-texto-tenue'}`}
          style={facet.status === 'thinking' ? { backgroundColor: colorToken(facet.token) } : {}}
        />
        <span
          className={`text-sm font-semibold capitalize truncate ${active ? '' : 'text-texto'}`}
          style={active ? { color: colorToken(facet.token) } : undefined}
        >
```
El tinte `+ '15'` bajo el nombre se va (regla de fondos opacos bajo texto). El hover inactivo cambia el borde en vez del fondo: `superficie-2` bajo el `text-texto-tenue` del estado es un par prohibido (spec §3.3). Las clases de `STATUS_DOT` y del resto del archivo, con la tabla M (`bg-gray-500` → `bg-texto-tenue`, `bg-green-400` → `bg-exito`, …).

- [ ] **Step 6: Message**

```jsx
import { FACET_TOKENS } from '../../store/useJaxStore'
import { colorToken } from '../../tema/tokens'

// dalle/user no son facetas (ver useJaxStore.js): extensión local para el chat.
const TOKEN_DE = { ...FACET_TOKENS, dalle: 'faceta-imagen', user: 'texto-suave' }

function Spinner({ token }) {
  return (
    <span
      className="inline-block w-3 h-3 rounded-full border-2 border-t-transparent animate-spin ml-1"
      style={{ borderColor: colorToken(token), borderTopColor: 'transparent' }}
    />
  )
}
…
  const token = TOKEN_DE[message.facet] || 'texto-suave'
…
      <div
        className="w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold mt-0.5 bg-superficie border"
        style={{ borderColor: colorToken(token, 0.38), color: colorToken(token) }}
      >
…
          <span className="text-xs font-semibold capitalize" style={{ color: colorToken(token) }}>
…
          {isRunning && <Spinner token={token} />}
          <span className="text-xs text-texto-tenue">
…
        <div
          className={`rounded-lg px-3 py-2 text-sm text-texto prose prose-invert prose-sm max-w-none ${isUser ? 'bg-burbuja-usuario' : 'bg-superficie'}`}
          style={{
            border: `1px solid ${colorToken(token, isRunning ? 0.38 : 0.12)}`,
            opacity: isRunning ? 0.85 : 1,
          }}
        >
```
(`prose`/`prose-invert` no hacen nada: `tailwind.config.js` no tiene el plugin de tipografía, `plugins: []`. Se dejan como están; no es alcance de este PR.)

- [ ] **Step 7: Verde, grep y commit**

```bash
cd /home/fruiz/worktrees/jax-platform-tema/frontend && npx vitest run 2>&1 | tail -3
cd /home/fruiz/worktrees/jax-platform-tema/frontend/src && grep -nE "(^|[^a-z-])(bg|text|border|divide|ring|placeholder|fill|stroke|from|via|to|outline|accent|caret|decoration|shadow)-((slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}|white|black)|(bg|text|border)-hal-|#[0-9a-fA-F]{3,8}([^0-9a-zA-Z]|$)|rgba?\( *[0-9]|(^|[^a-z-])dark:[a-z]|(fill|stroke)=[\"'](white|black)" store/useJaxStore.js components/HalEye/HalEye.jsx components/HalEye/HalEye.css components/LeftPanel/FacetCard.jsx components/CenterPanel/Message.jsx
git -C /home/fruiz/worktrees/jax-platform-tema add frontend/src/tema/tokens.js frontend/src/tema/contraste.test.js frontend/src/store/useJaxStore.js frontend/src/store/useJaxStore.eyeState.test.js frontend/src/components/HalEye/HalEye.jsx frontend/src/components/HalEye/HalEye.css frontend/src/components/LeftPanel/FacetCard.jsx frontend/src/components/CenterPanel/Message.jsx
git -C /home/fruiz/worktrees/jax-platform-tema commit -m "feat(tema): facetas y ojo HAL pintan con tokens; el store guarda nombres de token" -m "Spec §7.3 (PR 3). FACET_COLORS (hex) pasa a FACET_TOKENS; getEyeState devuelve token. HalEye, FacetCard y Message usan colorToken(); los tintes translúcidos bajo texto pasan a superficie opaca con borde del color. 9 pares nuevos fondo/faceta-* (texto oscuro sobre fondo de faceta): la matriz va de 88 a 97, 0 fallas." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
```
Expected: verde (+1); el grep sin salida.

---

### Task 21: BottomBar, KillSwitch y PipelineModal

**Files:** `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/components/BottomBar/BottomBar.jsx`, `frontend/src/components/BottomBar/KillSwitch.jsx`, `frontend/src/components/BottomBar/PipelineModal.jsx`

- [ ] **Step 1: RED** — los tres a `MIGRADOS`; `contraste.test.js` → FAIL nombrándolos.
- [ ] **Step 2: BottomBar** (con `import { colorToken } from '../../tema/tokens'`)
  - línea ~20: `color: facetsState[id]?.color || '#94a3b8'` → `token: facetsState[id]?.token || 'texto-suave'`
  - botón de faceta elegida (línea ~276): `style={activeFacet === f.id ? { backgroundColor: colorToken(f.token) } : {}}`, y en su `className` el texto del estado elegido pasa a `text-fondo` (par `fondo`/`faceta-*`); los no elegidos, tabla M.
  - aviso de imagen (línea ~298): se quita `style={{ color: '#7c3aed' }}` y se agrega la clase `text-faceta-imagen`.
  - selector de modo:
    ```jsx
                className={`px-2 py-1 rounded text-xs font-semibold transition-colors ${
                  mode === m
                    ? m === 'comando'
                      ? 'bg-modo-comando text-sobre-color'
                      : m === 'pipeline'
                      ? 'bg-texto-fuerte text-fondo'
                      : m === 'imagen'
                      ? 'bg-acento text-sobre-color'
                      : 'bg-accion text-sobre-color'
                    : 'bg-superficie text-texto-suave hover:text-texto'
                }`}
    ```
    y se borra su `style` del `#7c3aed`.
  - textarea: clases con la tabla M (`bg-superficie border-borde-control text-texto placeholder-texto-tenue focus:border-foco`) y:
    ```jsx
            style={{
              minHeight: '38px',
              borderColor: mode === 'comando' ? colorToken('modo-comando', 0.5)
                : mode === 'pipeline' ? colorToken('texto-fuerte', 0.25)
                : mode === 'imagen' ? colorToken('faceta-imagen', 0.5)
                : sending ? colorToken(activeFacetObj.token, 0.5) : undefined,
            }}
    ```
  - Enviar. El botón está `disabled` mientras `sending` (`disabled={!input.trim() || sending}`), así que `disabled:opacity-40` reemplaza al `+ 'aa'`:
    ```jsx
            className={`flex-shrink-0 px-4 py-2 rounded-lg disabled:opacity-40 text-sm font-semibold transition-colors ${
              mode === 'comando' ? 'bg-modo-comando text-sobre-color'
                : mode === 'pipeline' ? 'bg-texto-fuerte text-fondo'
                : mode === 'imagen' ? 'bg-acento text-sobre-color'
                : 'text-fondo'
            }`}
            style={['comando', 'pipeline', 'imagen'].includes(mode) ? undefined : { backgroundColor: colorToken(activeFacetObj.token) }}
    ```
    **Cambio visible:** en modo chat, el texto de Enviar pasa de blanco a oscuro (blanco sobre `faceta-jax-local` daba 2,54:1).
- [ ] **Step 3: PipelineModal**
  - línea ~28: `token: facetsState[f.id]?.token || 'texto-suave'`
  - velo: se quita `style={{ backgroundColor: 'rgba(0,0,0,0.7)' }}` y se agrega `bg-fondo/70` al `className`; panel `bg-slate-900 border-slate-700` → `bg-fondo border-borde`
  - etiqueta de faceta: `className` `border-borde bg-superficie` en los dos estados (+ `hover:border-borde-control` si no está elegida), `style={selected.includes(f.id) ? { borderColor: colorToken(f.token, 0.5) } : {}}`
  - tilde: elegida `style={{ borderColor: colorToken(f.token), backgroundColor: colorToken(f.token) }}` + clase `text-fondo`; no elegida, clase `border-borde-control` y sin `style`
  - nombre: `style={{ color: colorToken(f.token) }}`; descripción `text-slate-500` → `text-texto-tenue`
  - Planificar y ejecutar: se quita `style={{ backgroundColor: '#3b82f6' }}` y la clase queda `bg-accion hover:bg-accion-hover text-sobre-color` (sin `text-white`)
  - el resto, tabla M.
- [ ] **Step 4: KillSwitch** — tabla M (`bg-red-600 text-white` → `bg-peligro-solido text-sobre-color`, `hover:bg-red-500` → `hover:bg-peligro-solido-hover`, `bg-red-950`/`bg-red-900` con texto → `bg-peligro-fondo`, `border-red-600`/`border-red-500` → `border-peligro-solido`).
- [ ] **Step 5: Verde + grep** sobre los tres, más `grep -rn "\.color\b" frontend/src --include=*.jsx | grep -v test` → sin salida. **Commit** (`feat(tema): barra inferior, interruptor de emergencia y modal de pipeline con tokens`; el cuerpo nombra el cambio de texto de Enviar; trailers).

---

### Task 22: CenterPanel, adjuntos, LeftPanel y Toast

**Files:** `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/components/CenterPanel/CenterPanel.jsx` (1), `frontend/src/components/chat/AttachButton.jsx` (5), `frontend/src/components/chat/FileAttachment.jsx` (7), `frontend/src/components/LeftPanel/LeftPanel.jsx` (16), `frontend/src/components/Notifications/Toast.jsx` (12)

- [ ] **Step 1: RED** — los cinco a `MIGRADOS`; FAIL nombrándolos.
- [ ] **Step 2: Tabla M.** `CenterPanel`: `bg-hal-bg` → `bg-fondo`. `Toast`: cada tipo es texto de estado sobre su fondo: `bg-red-900 text-red-200 border-red-700` → `bg-peligro-fondo text-peligro border-peligro-borde`; verde → `exito*`; `bg-blue-900 text-blue-200 border-blue-600` → `bg-info-fondo text-info border-accion`; `bg-amber-900 text-amber-200 border-amber-600` → `bg-aviso-fondo text-aviso border-aviso-borde`. `LeftPanel`: `text-emerald-400` → `text-exito`; `text-amber-500/80` → `text-aviso`.
- [ ] **Step 3: Verde + grep** sobre los cinco; **commit** (`feat(tema): panel central, adjuntos, panel izquierdo y avisos con tokens`, trailers).

---

### Task 23: RightPanel, AuditLog y StepCard

**Files:** `frontend/src/tema/tokens.js` (`MIGRADOS`), `frontend/src/components/RightPanel/RightPanel.jsx` (25 + 1 hex), `frontend/src/components/RightPanel/AuditLog.jsx` (18), `frontend/src/components/RightPanel/StepCard.jsx` (12)

- [ ] **Step 1: RED** — los tres a `MIGRADOS`; FAIL nombrándolos.
- [ ] **Step 2:** barra de progreso de RightPanel (sin texto): pista `bg-slate-800` → `bg-superficie`; relleno `style={{ width: `${pct}%` }}` + clase `bg-accion` (se quita el `'#3b82f6'`). Botones Aprobar/Cancelar y el aviso de error (lote de deuda del 2026-09-14): tabla M. `AuditLog`: `text-blue-300` → `text-info`; `bg-blue-500/20` (sin texto) → `bg-info/20`; `bg-orange-500/20` → `bg-modo-comando/20`; `bg-orange-600` con texto → `bg-modo-comando text-sobre-color`. `StepCard`: estados con tabla M; `bg-yellow-500` (punto) → `bg-aviso`.
- [ ] **Step 3: Verde + grep** (RightPanel.test incluido); **commit** (`feat(tema): panel derecho, bitácora y pasos con tokens`, trailers).

---

### Task 24: Se borra `lightModeOverrides.test.js` y los overrides que quedaron sin uso

**Files:** Delete `frontend/src/lightModeOverrides.test.js`; Modify `frontend/src/index.css`

Con el chat migrado no queda rojo ni verde tintado en `src` (Dashboard no los usa), así que el test viejo ya no guarda nada. Su reemplazo, el test de contraste, está desde el PR 1 (spec §6, [H] 7).

- [ ] **Step 1:** `grep -rnE "(text|bg|border)-(red|green)-[0-9]" /home/fruiz/worktrees/jax-platform-tema/frontend/src --include=*.jsx --include=*.js | grep -v test` → **sin salida**. Si queda algo, no se borra el test: se migra ese archivo primero.
- [ ] **Step 2:** `git -C /home/fruiz/worktrees/jax-platform-tema rm frontend/src/lightModeOverrides.test.js`; borrar de `index.css` el bloque "Rojos y verdes tintados…" entero y todo override que el script de la Task 18 Step 1 liste como sin uso. Correr el script otra vez → `sin uso []`. Lo que queda son sólo las clases de Dashboard (`bg-hal-bg`, `text-hal-text`, `bg-slate-900`, `border-slate-700`) y las que el script vea en uso.
- [ ] **Step 3:** `npx vitest run` → verde con **un test menos** que antes de esta task. **Commit** (`refactor(tema): se va lightModeOverrides.test.js; la capa vieja queda sólo para Dashboard`, con el conteo de overrides antes/después en el cuerpo, trailers).

---

### Task 25: Cierre del PR 3

Procedimiento C con `<RAMA>` = `feat/tema-tokens-chat` (creada en la Task 20 Step 0), `<GRUPO>` = los 16 archivos del grupo, `<DELTA>` = **0** (+1 del ojo, −1 de `lightModeOverrides.test.js`), `<ETIQUETA>` = `pr3`. En vivo: chat con cada faceta (etiqueta, avatar, burbuja propia y ajena, spinner), el ojo en reposo, pensando, gate y kill switch (si Fernando lo puede provocar sin riesgo; si no, se anota como no ejercitado), modos chat/comando/pipeline/imagen con Enviar, el modal de pipeline (elegir facetas, cadena, error), el panel derecho con un pipeline corriendo y uno esperando aprobación, un aviso (Toast), adjuntar un archivo.

---

# PR 4 · El resto y cierre (Tasks 26-28)

Grupo medido en `bfab4de`: `pages/Dashboard.jsx` (`bg-hal-bg`, `text-hal-text`, `bg-slate-900`, `border-slate-700`). Más: el escaneo pasa de `MIGRADOS` a **todos** los fuentes y a los `.css`; se borra lo que queda de la capa `html.light-mode`, la clase `light-mode` y `hal`.

### Task 26: Dashboard, escaneo de todo `src` y fin de la capa vieja

**Files:** Modify `frontend/src/pages/Dashboard.jsx`, `frontend/src/tema/tokens.js` (se borra `MIGRADOS`), `frontend/src/tema/contraste.test.js`, `frontend/src/index.css`

- [ ] **Step 1: Rama:** `git -C /home/fruiz/worktrees/jax-platform-tema switch -c feat/tema-tokens-cierre origin/master` (con el PR 3 adentro); `npx vitest run` verde.
- [ ] **Step 2: RED — el escaneo pasa a todos.** En `contraste.test.js`: se quita `MIGRADOS` del import; en los dos tests de uso, `for (const ruta of MIGRADOS)` → `for (const ruta of Object.keys(porRuta))`; el `expect(MIGRADOS.length).toBeGreaterThan(0)` pasa a `expect(Object.keys(porRuta).length).toBeGreaterThan(40)` (hoy hay 47 `.jsx` más los `.js`: si el glob no encuentra nada, rojo). Y un test más para las hojas de estilo, que se leen por `fs` porque `?raw` de CSS llega vacío en vitest:

```js
import { readdirSync } from 'node:fs'

it('ninguna hoja de estilo fuera de tokens.css tiene hex ni rgb literal', () => {
  const raiz = new URL('../', import.meta.url)
  const hojas = readdirSync(raiz, { recursive: true })
    .filter((f) => f.endsWith('.css') && f.replaceAll('\\', '/') !== 'tema/tokens.css')
  expect(hojas.length).toBeGreaterThan(1) // index.css y HalEye.css
  const hallazgos = []
  for (const hoja of hojas) {
    const css = readFileSync(new URL(hoja, raiz), 'utf8')
    for (const m of css.match(/#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z])|rgba?\(\s*\d/g) || []) hallazgos.push(`${hoja}: «${m}»`)
  }
  expect(hallazgos).toEqual([])
})
```
Borrar `MIGRADOS` de `tokens.js` (y su comentario). Run → FAIL: `pages/Dashboard.jsx` (`bg-hal-bg`, `bg-slate-900`…) y `index.css` (los hex de la capa vieja).

- [ ] **Step 3: Dashboard:** `bg-hal-bg text-hal-text` → `bg-fondo text-texto`; encabezado `bg-slate-900 border-b border-slate-700` → `bg-hundido border-b border-borde`.
- [ ] **Step 4: `index.css`:** se borra **toda** la capa `html.light-mode` (el comentario "LIGHT MODE (capa vieja)" y cada regla). Quedan `@import` de tokens, `@tailwind`, `body`, `*` y las barras con tokens.
- [ ] **Step 5: Verde + grep** sobre **todo** `frontend/src` (el comando de la tabla M con `-r` y `--include=*.js --include=*.jsx --include=*.css --exclude=*.test.* --exclude=tokens.css` en lugar de la lista de archivos) → sin salida, salvo las entradas de `PERMITIDOS_CRUDOS` si alguna se aprobó. **Commit** (`feat(tema): Dashboard con tokens; el escaneo cubre todo src y las hojas de estilo; se borra la capa html.light-mode`, trailers).

---

### Task 27: Adiós a la clase `light-mode` y a `hal`

**Files:** Modify `frontend/src/tema/aplicarTema.js`, `frontend/index.html`, `frontend/src/store/useTema.test.js`, `frontend/tailwind.config.js`, `frontend/src/tema/tailwind.test.js`, `frontend/src/tema/contraste.test.js`

- [ ] **Step 1: RED** — en `contraste.test.js`, agregar a `PROHIBIDOS` `['clase de la capa vieja', /light-mode/g]` y un test que lea `index.html` por `fs` y exija `not.toContain('light-mode')`; en `tailwind.test.js`, `expect(colores.hal).toBeUndefined()`. Run → FAIL en `tema/aplicarTema.js`, `index.html` y `hal`.
- [ ] **Step 2:** `aplicarTema` queda:

```js
// data-tema="claro" es lo que leen los tokens (src/tema/tokens.css).
export function aplicarTema(tema, raiz = document.documentElement) {
  if (tema === 'light') raiz.setAttribute('data-tema', 'claro')
  else raiz.removeAttribute('data-tema')
}
```
En el script de `index.html` se borra la línea `document.documentElement.classList.add('light-mode')`. El `raizFalsa` de `scriptTema.test.js` sigue sirviendo sin cambios (compara attrs y clases; ahora las dos quedan vacías). En `useTema.test.js` se borran las dos aserciones sobre `classList.contains('light-mode')`. En `tailwind.config.js` se borra `hal` (y su comentario).
- [ ] **Step 3:** `grep -rn "light-mode\|hal-" /home/fruiz/worktrees/jax-platform-tema/frontend/src /home/fruiz/worktrees/jax-platform-tema/frontend/index.html /home/fruiz/worktrees/jax-platform-tema/frontend/tailwind.config.js` → sólo `hal-eye`/`hal-anim`/`hal-glow`/`hal-iris` (clases CSS propias de HalEye, no colores). `npx vitest run` verde (+1). **Commit** (`refactor(tema): sin clase light-mode ni colores hal: el tema es sólo data-tema`, trailers).

---

### Task 28: Cierre del PR 4

Procedimiento C con `<RAMA>` = `feat/tema-tokens-cierre`, `<GRUPO>` = Dashboard + `index.css` + `index.html` + `aplicarTema.js` + `tailwind.config.js`, `<DELTA>` = **+2** (el test de hojas de estilo de la Task 26 y el de `index.html` sin `light-mode` de la Task 27; se pone el número medido), `<ETIQUETA>` = `pr4`. En vivo: recorrer **todas** las pantallas de los PRs 1-3 en los dos temas (la capa vieja ya no existe: cualquier clase olvidada se vería acá) y el chequeo mecánico de la Task 12 Step 6.5 en cada una → `[]`. Un navegador que tenía guardado `jax_theme=light` desde antes del PR 1 sigue en claro después del deploy (la clave no cambió).

---

# Cierre en la Biblioteca (Task 29)

### Task 29: DEUDA.md en `jax` — PR propio

**Files:** `/home/fruiz/worktrees/jax-deuda-tema/DEUDA.md` (worktree nuevo de `jax`; **nunca** se edita `/home/fruiz/jax/DEUDA.md` en su lugar)

- [ ] **Step 1: Worktree**

```bash
git -C /home/fruiz/jax fetch origin
git -C /home/fruiz/jax worktree add /home/fruiz/worktrees/jax-deuda-tema -b docs/deuda-tema-tokens origin/master
git -C /home/fruiz/worktrees/jax-deuda-tema log --oneline -1
```

- [ ] **Step 2: Medir antes de escribir (CONTEXT.md §7: todo ítem se mide contra el árbol antes de tocarlo).** Con master de jax-platform después del PR 4: `grep -rn "light-mode" frontend/src frontend/index.html` → 0; el test de contraste en verde con 97 pares; `curl -s https://axioma-ia.io/login | grep -o 'index-[A-Za-z0-9_-]*\.js'` y el md5 servido igual al local. Esa es la evidencia que se cita; si algo no da, no se escribe CERRADO.

- [ ] **Step 3: Editar DEUDA.md** (los ítems se ubican por el texto; las líneas del 2026-09-14 eran ~2586, ~2597, ~2602 y ~2690). Ninguno se borra en silencio: el texto original queda citado.
  1. **"El frontend no tiene tema claro/oscuro en ninguna pantalla."** → `**El frontend no tiene tema claro/oscuro — CORREGIDO 2026-09-14 (el ítem era falso tal como estaba escrito: existía un modo claro manual, la capa html.light-mode de index.css y el interruptor de la barra; lo verdadero era que no había tokens ni garantía de contraste) y CERRADO Y DESPLEGADO <fecha>** (jax-platform#<PR1> → <sha>, #<PR2> → <sha>, #<PR3> → <sha>, #<PR4> → <sha>; frontend <index-*.js>, md5 local = servido; backup <nombre>). 47 tokens en src/tema/tokens.css, 97 pares AA verificados en los dos temas por src/tema/contraste.test.js en CI, escaneo de todo src sin colores crudos; theme_default funciona (GET /api/apariencia + script en línea). Texto original: <el párrafo de antes, entre comillas>.`
  2. **"Contraste de textos secundarios en modo oscuro"** (`text-slate-500` 3,75:1) → `CERRADO <fecha>`: `texto-tenue` = `#8190a6` (4,51 sobre superficie, 5,50 sobre fondo), exigido por el test.
  3. **"Pares de contraste por debajo de 4,5 que hoy no se usan"** → `CERRADO <fecha>`: la capa de overrides se borró; el test mide contraste en lugar de exigir que exista un override.
  4. **"Fuente Inter"** → `CERRADO <fecha> — DECISIÓN de Fernando 2026-09-14`: Inter se carga con `@fontsource/inter` 5.3.0 (latin 400-700, `font-display: swap`); la advertencia del hook de impeccable queda registrada en `frontend/.impeccable/config.json` con el motivo "user confirmed: Fernando 2026-09-14".
  5. **Nuevo, junto a las otras entradas de carga:** `**Carga de GET /api/apariencia — VERDAD OPERACIONAL, <fecha y hora> CST.**` con la tabla de la Task 11 Step 4 (c=1/10/30/100: rps, p50, p95, p99, errores; `/api/health` como referencia), el sha y "127.0.0.1:8091 contra jax_memory_test". Y `**Bundle y primer pintado del tema — VERDAD OPERACIONAL**` con JS/CSS crudo y gzip y fuentes antes del PR 1 y después de cada PR, y FCP/CLS del Login por tema con el método (Lighthouse o panel Performance). Cada número copiado del registro `.superpowers/sdd/tema-tokens-medidas.md`, no de memoria.

- [ ] **Step 4: Commit, PR y gate**

```bash
git -C /home/fruiz/worktrees/jax-deuda-tema add DEUDA.md
git -C /home/fruiz/worktrees/jax-deuda-tema commit -m "docs(deuda): tema claro/oscuro corregido y cerrado; contraste e Inter cerrados; carga de /api/apariencia" -m "El ítem del tema era falso tal como estaba escrito (existía una capa manual): se marca CORREGIDO y CERRADO con la evidencia de los cuatro PRs de jax-platform. Números de carga, bundle y primer pintado como VERDAD OPERACIONAL con fecha." -m "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LNXrHCwsZAVfrQj56maFHB"
git -C /home/fruiz/worktrees/jax-deuda-tema push -u origin docs/deuda-tema-tokens
gh pr create --repo fjruizhn/jax --base master --head docs/deuda-tema-tokens --title "DEUDA: tema con tokens cerrado" --body-file <scratchpad>/deuda-cuerpo.md
gh pr checks <N> --repo fjruizhn/jax
gh pr view <N> --repo fjruizhn/jax --json headRefOid -q .headRefOid
git -C /home/fruiz/worktrees/jax-deuda-tema ls-remote origin refs/heads/docs/deuda-tema-tokens
```
Todos los checks `SUCCESS` y los shas iguales → merge (`gh pr merge <N> --repo fjruizhn/jax --merge`). Con el GO de Fernando, actualizar el checkout de producción **sólo si está limpio y en master**: `git -C /home/fruiz/jax status --short` (vacío) → `git -C /home/fruiz/jax pull --ff-only`. Borrar el worktree: `git -C /home/fruiz/jax worktree remove /home/fruiz/worktrees/jax-deuda-tema`.

---

## Hallazgos fuera de alcance (anotados, no se tocan en este plan)

- `pages/Login.jsx` trae el placeholder del correo hardcodeado (`fernando@rich-hn.com`), fuera de i18n. No es color ni tema; se le lleva a Fernando para que decida si entra al PR 1 como clave `loginEmailPlaceholder` en es/en.
- `prose prose-invert` en `Message.jsx` no hace nada: no hay plugin de tipografía. No es alcance de un PR de tokens.

## Autorrevisión (contra el spec, 2026-09-14)

- **Cobertura del spec:** §3 tokens → Task 2; §3.3 matriz → Task 2 (88) y Task 20 (97, con el porqué); §4 Tailwind → Task 3; §5.1-5.2 semántica, endpoint, script, sincronización, AdminSettings → Tasks 4, 5, 6, 7; §5.2 CSP → Task 12 Step 5; §5.3 hook → Task 5 (store, desvío declarado en el Mapa); §6.1-6.5 test → Tasks 2 y 9, y 26 para "todos"; §6 `lightModeOverrides.test.js` hasta el PR 2/3 → Tasks 18 y 24; §7.1 → tabla M; §7.2 constantes → Tasks 9 y 14; §7.3 hex y en línea → Tasks 9, 17, 20, 21, 23; §8 rollout y orden con la etapa 2 → Tasks 1, 13, 20, 26; §9 LAS CUATRO → Tasks 1, 6 (EXPLAIN), 8 (swap), 11 (carga), C.2; §10 pruebas → Tasks 2-9, 20, 26-27; §1.3 DEUDA → Task 29; §2.3 Inter e impeccable → Task 8.
- **Placeholders:** los únicos valores abiertos son números que **se miden** en la ejecución (`<vigente>`, `<medido>`, `<N>`, shas, fechas). Van marcados así a propósito: escribirlos ahora sería inventarlos.
- **Consistencia de nombres:** `TOKENS`, `PARES`, `AA_TEXTO`, `AA_UI`, `EXENTOS_TEXTO`, `PERMITIDOS_CRUDOS`, `MIGRADOS` (hasta la Task 26), `colorToken`, `tokenDeFaceta`, `parsearTokens`, `contraste`, `CLAVE_ELECCION`, `CLAVE_PREDETERMINADO`, `esTema`, `resolverTema`, `temaInicial`, `aplicarTema`, `useTema.{theme, predeterminado, toggleTheme, fijarPredeterminado, sincronizarPredeterminado}`, `FACET_TOKENS`, `getEyeState → { token, animation, label }`, `CONSULTA`/`CLAVE`/`TEMAS` en `api/apariencia.py`: definidos una vez, usados con el mismo nombre.
- **Verificado al escribir** (scratchpad, 2026-09-14): la matriz de 88 da 0 fallas y el control 3,75; el script en línea equivale a `aplicarTema` en las 5 combinaciones; el regex de uso encuentra 58/34/7/2/1/0 en los archivos del PR 1 y nada en `tokens.js`; el script de overrides sin uso encuentra 51 overrides y 1 sin uso.
- **Contradicciones spec ↔ árbol, anotadas donde aplican:** el `toHaveClass` de PasswordInput no es de color (Task 9); la entrada de Inter en `.impeccable/config.json` ya existía (Task 8); `HalEye.css` tiene un hex que el inventario de `.jsx` del spec no contó (Task 20); fondos de faceta y tintes bajo texto sin par en el spec (PR 3); `useTema` como store y no como hook con estado local (Mapa); `node_modules` symlink y dependencia nueva (Global Constraints y Task 8); la etapa 2 aún sin mergear, con los pisos ya en 633/303 en su rama (Global Constraints y Task 1).
