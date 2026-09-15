# Tema claro/oscuro con tokens semánticos — diseño

- **Fecha:** 2026-09-14
- **Autor:** Mr. Hyde (Claude Code), para Fernando Ruiz
- **Estado:** diseño para revisión del dueño. Sin código todavía.
- **Rama:** `feat/tema-tokens` (worktree `/home/fruiz/worktrees/jax-platform-tema`), base `origin/master` `bfab4de`.
- **Origen de autoridad:** las decisiones de §2 son de Fernando (2026-09-14). El inventario de §1.2 lo midió
  la sesión que despachó este spec (2026-09-14) y lo verifiqué contra el árbol en `bfab4de`. Los contrastes de §3 los
  calculé yo con la fórmula de luminancia relativa de WCAG 2.x (script en el scratchpad de la sesión; el test
  de §6 lo reemplaza como fuente de verdad).

---

## 1. Por qué

### 1.1 El problema

La política del ecosistema dice "Dark/Light mode — SIEMPRE" y "colores → CSS variables o tokens, nunca
valores hardcodeados". El frontend de jax-platform no cumple ninguna de las dos: cada pantalla pinta con
clases fijas de Tailwind (`bg-slate-800`, `text-red-400`) y con hex sueltos, y el modo claro existe sólo
porque una capa de CSS en `frontend/src/index.css:33-125` **sobrescribe clase por clase** bajo
`html.light-mode`. Esa capa:

- no escala: cada clase nueva que usa una pantalla necesita su override a mano, y si falta, esa pantalla
  queda ilegible en claro (medido el 2026-09-13: el aviso de SMTP corrupto daba 1,49:1);
- no cubre ni los hex de los `.jsx` ni los `style={{}}` en línea (Login, HalEye, burbujas del chat, barra
  inferior);
- tampoco garantiza contraste: `lightModeOverrides.test.js` exige que el override **exista**, no que se
  lea. En oscuro, además, `text-slate-500` da 3,75:1 sobre el fondo (falla AA) y nadie lo mide.

Y `theme_default`, que el superadmin edita en `pages/admin/AdminSettings.jsx:80` y el backend guarda en
`axioma_config` (`backend/api/admin/config_admin.py:14`), **no hace nada**: el frontend nunca lo lee. El tema
sale de `localStorage['jax_theme']` con `'dark'` fijo por defecto (`store/useTheme.js:8`, `main.jsx:9-10`).

### 1.2 Inventario medido (2026-09-14, `bfab4de`)

| Qué | Medida |
|---|---|
| Clases `dark:` / variables CSS / `darkMode` en Tailwind | 0 / 0 / no existe |
| Capa manual de claro | `index.css:33-125`, selectores `html.light-mode .clase` |
| Interruptor | `store/useTheme.js` (hook con estado local, sin store), aplicado antes del render en `main.jsx:8-11` |
| Clases de color | ~693 en 32 de 47 `.jsx` (slate 453, red 81, purple 54, blue 41, green 36, orange 12, amber 8, yellow 4, gray 3, emerald 1) |
| Archivos con más clases | AdminMotors 69, AdminFacetsModels 51, AdminUsers 49, PipelineModal 49, Login 46, AdminModelCatalog 42, AdminSmtp 41, AdminSettings 33, AdminRepository 31, ResetPassword 30 |
| Hex en `.jsx`/`.js` | 51 en 10 archivos (p. ej. `Login.jsx:149-153`, `AdminCosts.jsx:29`, `FACET_COLORS` y `getEyeState` en `store/useJaxStore.js:71-78, 498-518`) |
| Colores en `style={{}}` | 20 en 7 archivos: HalEye, FacetCard, Message, PipelineModal, BottomBar, AdminCosts, AdminFacetBindings |
| Grises bajo AA en oscuro | `text-slate-500` 46 usos (3,75:1 sobre `#0f172a`), `text-slate-600` 26, `text-slate-700` 6 |
| Constantes de estilo compartidas | `INPUT`/`LABEL`/`BOTON` en `pages/admin/AdminSmtp.jsx:20-22`; `BOTON`/`BOTON_NEUTRO` en `components/BarraUsuario.jsx:67-68`; `components/AlertaError.jsx` (escrito para ser el único lugar a migrar) |
| Tests acoplados a clases | 1 (`PasswordInput.test.jsx`, `toHaveClass`); ningún test compara hex (grep sobre `*.test.*`: 0) |
| Fuente | `index.css:8` declara `'Inter'` pero **no se carga**: se ve Segoe UI o la sans del sistema. La marca sí carga IBM Plex Serif vía `@fontsource` (`LogoAxioma.jsx:5-6`) |
| Pesos tipográficos usados | `font-semibold` 81, `font-bold` 30, `font-medium` 7, `font-light` 1 (este último en la marca, Plex Serif) |
| Bundle servido hoy | `index-BK3OWw2W.js` 551.634 B (168.720 gzip -9), `index-RzDeoEvO.css` 29.102 B (6.170 gzip -9), medido sobre `/home/fruiz/jax-platform/frontend/dist` |

### 1.3 Corrección a DEUDA

`jax/DEUDA.md:2690` dice: *"El frontend no tiene tema claro/oscuro en ninguna pantalla"*. **Es falso tal como
está escrito** (tipo HECHO, corregido 2026-09-14): existe un modo claro manual (capa `html.light-mode` e
interruptor en la barra de usuario). Lo verdadero es que **no hay tokens** y que el claro depende de
overrides clase por clase, sin garantía de contraste. Al cerrar el PR 1 el ítem se reescribe marcado como
corregido (no se borra en silencio) y queda abierto hasta el PR 4. El ítem de Inter (`DEUDA.md:2602-2604`,
"se decide en el Lote 3") se cierra con la decisión 3 de §2.

---

## 2. Decisiones (Fernando, 2026-09-14, vinculantes)

1. **Tokens semánticos.** Variables CSS con valor claro y oscuro, mapeadas en `tailwind.config.js`; las
   pantallas usan `bg-fondo`/`text-texto`, no `slate-800`. La capa `html.light-mode` de `index.css` **se
   borra** a medida que se migra, y del todo en el PR 4.
2. **`theme_default` funciona.** Quien nunca eligió tema ve el configurado; quien eligió, conserva el suyo.
3. **Inter se carga de verdad**, con `@fontsource/inter`, igual que Plex Serif. El hook de diseño
   *impeccable* marca Inter como "sobreusada": **el dueño la eligió igual; queda registrado como DECISIÓN**
   y no se reabre por la advertencia del hook.
4. **WCAG AA en los dos temas** (4,5:1 texto normal; 3:1 texto grande y componentes de UI), exigido por un
   **test automático** que calcula el contraste de cada par texto/fondo declarado. Reemplaza a
   `lightModeOverrides.test.js`.
5. **Rollout en 4 PRs**: base + Login + barra de usuario; admin; chat y pipelines; el resto. Tokens y clases
   viejas conviven hasta que cada grupo migra. Cada PR se revisa y despliega por separado.
6. **Paleta: se conserva el look.** Oscuro como hoy (grises slate, acento violeta, el oro de la marca); el
   claro se deriva con AA; los grises que fallan AA suben **lo justo**.

Decisiones que tomé yo dentro de ese marco, cada una con su razón, están marcadas **[H]** en el texto y
resumidas en §12.

---

## 3. Diseño de los tokens

### 3.1 Forma

- Cada token es una variable CSS con los **canales RGB separados por espacio** (`--fondo: 15 23 42;`), para
  que Tailwind pueda aplicar opacidad: `rgb(var(--fondo) / <alpha-value>)`. Así `bg-superficie/50` sigue
  funcionando.
- **Oscuro es el valor de `:root`** (es el tema de hoy y el `DEFAULT_CONFIG`); claro es
  `:root[data-tema="claro"]`. **[H]** Atributo `data-tema` en `<html>` en lugar de la clase `light-mode`:
  así nada choca con la capa vieja mientras conviven (la capa sigue colgada de `html.light-mode` y el
  interruptor pone **las dos** marcas hasta el PR 4, que quita la clase).
- `color-scheme: dark` en `:root` y `color-scheme: light` en el claro, para que los controles nativos
  (`<select>`, barras de desplazamiento, autocompletado) sigan el tema.
- Los tokens viven en un archivo propio, `frontend/src/tema/tokens.css`, importado primero desde
  `index.css`. **Es la única fuente de verdad de los valores**: Tailwind y el test lo leen de ahí.
- Los tokens son **opacos**. Los fondos "tintados" de hoy (`bg-red-900/30` sobre una superficie) se
  convierten en un token opaco con el color ya compuesto (`--peligro-fondo`), porque el test sólo puede
  medir contra un fondo conocido. Usar opacidad de Tailwind sobre un token de fondo (`bg-peligro/20`) está
  permitido **sólo** para superficies sin texto encima (barras de progreso, halos); la regla está en §7.

### 3.2 Lista de tokens (47)

Oscuro = hoy (salvo los marcados ↑, que suben para pasar AA). Claro = derivado. Cada celda: hex · canales.

| Token | Oscuro | Claro |
|---|---|---|
| `--fondo` | `#0f172a` · `15 23 42` | `#f8fafc` · `248 250 252` |
| `--superficie` | `#1e293b` · `30 41 59` | `#f1f5f9` · `241 245 249` |
| `--superficie-2` | `#334155` · `51 65 85` | `#e2e8f0` · `226 232 240` |
| `--hundido` | `#0f172a` · `15 23 42` | `#ffffff` · `255 255 255` |
| `--borde` | `#334155` · `51 65 85` | `#cbd5e1` · `203 213 225` |
| `--borde-control` | `#63738a` · `99 115 138` | `#7f8fa5` · `127 143 165` |
| `--barra-pista` | `#1e293b` · `30 41 59` | `#e2e8f0` · `226 232 240` |
| `--barra-pulgar` | `#334155` · `51 65 85` | `#94a3b8` · `148 163 184` |
| `--texto-fuerte` | `#f1f5f9` · `241 245 249` | `#020617` · `2 6 23` |
| `--texto` | `#e2e8f0` · `226 232 240` | `#0f172a` · `15 23 42` |
| `--texto-suave` | `#94a3b8` · `148 163 184` | `#475569` · `71 85 105` |
| `--texto-tenue` | `#8190a6` · `129 144 166` | `#617188` · `97 113 136` |
| `--sobre-color` | `#ffffff` · `255 255 255` | `#ffffff` · `255 255 255` |
| `--acento` | `#9333ea` · `147 51 234` | `#9333ea` · `147 51 234` |
| `--acento-hover` | `#7e22ce` · `126 34 206` | `#7e22ce` · `126 34 206` |
| `--acento-texto` | `#c084fc` · `192 132 252` | `#7e22ce` · `126 34 206` |
| `--acento-fondo` | `#352459` · `53 36 89` | `#faf5ff` · `250 245 255` |
| `--accion` | `#2563eb` · `37 99 235` | `#2563eb` · `37 99 235` |
| `--accion-hover` | `#1d4ed8` · `29 78 216` | `#1d4ed8` · `29 78 216` |
| `--info` | `#60a5fa` · `96 165 250` | `#1d4ed8` · `29 78 216` |
| `--info-fondo` | `#1e2e53` · `30 46 83` | `#eff6ff` · `239 246 255` |
| `--foco` | `#3b82f6` · `59 130 246` | `#3b82f6` · `59 130 246` |
| `--peligro` | `#f87171` · `248 113 113` | `#b91c1c` · `185 28 28` |
| `--peligro-fondo` | `#3b2532` · `59 37 50` | `#fef2f2` · `254 242 242` |
| `--peligro-borde` | `#991b1b` · `153 27 27` | `#fca5a5` · `252 165 165` |
| `--peligro-solido` | `#dc2626` · `220 38 38` | `#dc2626` · `220 38 38` |
| `--peligro-solido-hover` | `#b91c1c` · `185 28 28` | `#b91c1c` · `185 28 28` |
| `--exito` | `#4ade80` · `74 222 128` | `#15803d` · `21 128 61` |
| `--exito-fondo` | `#1b3637` · `27 54 55` | `#f0fdf4` · `240 253 244` |
| `--exito-borde` | `#166534` · `22 101 52` | `#86efac` · `134 239 172` |
| `--aviso` | `#fbbf24` · `251 191 36` | `#b45309` · `180 83 9` |
| `--aviso-fondo` | `#392d2e` · `57 45 46` | `#fffbeb` · `255 251 235` |
| `--aviso-borde` | `#92400e` · `146 64 14` | `#fcd34d` · `252 211 77` |
| `--oro` | `#c9a84c` · `201 168 76` | `#7a5f24` · `122 95 36` |
| `--oro-claro` | `#e8d5a3` · `232 213 163` | `#896c2f` · `137 108 47` |
| `--oro-oscuro` | `#8a6d2f` · `138 109 47` | `#a08a55` · `160 138 85` |
| `--burbuja-usuario` | `#1d385c` · `29 56 92` | `#dbeafe` · `219 234 254` |
| `--modo-comando` | `#c2410c` · `194 65 12` | `#c2410c` · `194 65 12` |
| `--faceta-jax-local` | `#60a5fa` · `96 165 250` | `#1d4ed8` · `29 78 216` |
| `--faceta-jekyll` | `#818cf8` · `129 140 248` | `#4338ca` · `67 56 202` |
| `--faceta-hyde` | `#f97316` · `249 115 22` | `#c2410c` · `194 65 12` |
| `--faceta-hipatia` | `#10b981` · `16 185 129` | `#047857` · `4 120 87` |
| `--faceta-thot` | `#f59e0b` · `245 158 11` | `#b45309` · `180 83 9` |
| `--faceta-kimi` | `#06b6d4` · `6 182 212` | `#0e7490` · `14 116 144` |
| `--faceta-ada` | `#a78bfa` · `167 139 250` | `#6d28d9` · `109 40 217` |
| `--faceta-jacobs` | `#ffffff` · `255 255 255` | `#0f172a` · `15 23 42` |
| `--faceta-imagen` | `#a78bfa` · `167 139 250` | `#6d28d9` · `109 40 217` |

Notas por grupo:

- **Superficies.** `fondo` = `hal-bg`/`slate-900`; `superficie` = tarjetas (`slate-800`); `superficie-2` =
  hover y botones neutros (`slate-700`); `hundido` = campos de formulario (hoy `bg-slate-900` dentro de una
  tarjeta). Los valores claros son los de la capa manual de hoy (`index.css:47-51`), para no cambiar el
  aspecto del claro que ya se usa.
- **`borde`** es decorativo (separa, no identifica): no se le exige 3:1. **`borde-control`** es el borde de
  un campo o control interactivo y **sí** se le exige 3:1 (WCAG 1.4.11). ↑ En oscuro sube de `slate-600`
  (`#475569`, 2,36:1 sobre el fondo) a `#63738a`, el primer paso de la interpolación `slate-600→slate-500`
  que alcanza 3:1 sobre `superficie`. En claro, `#7f8fa5` (entre `slate-400` y `slate-500`), el mínimo que
  alcanza 3:1 sobre `superficie`.
- **Texto.** `texto-fuerte` = `slate-100`, `texto` = `slate-200` (hoy `hal-text`), `texto-suave` =
  `slate-400`. ↑ **`texto-tenue`** reemplaza a `slate-500/600/700`: en oscuro `#8190a6` (el primer punto de
  `slate-500→slate-400` que da 4,5:1 sobre `superficie`: 4,51), en claro `#617188` (el primero de
  `slate-500→slate-600` que da 4,5:1 sobre `superficie`: 4,54). Es el cambio visible más grande del oscuro y
  es la decisión 6 aplicada: **suben lo justo**.
- **Acento y acción.** El violeta (`purple-600`) es el acento de selección y foco de admin; el azul
  (`blue-600`) es el botón primario de Login y Reset. Se conservan los dos papeles. ↑ **`accion-hover`**: hoy
  el hover es `blue-500`, que con texto blanco da **3,68:1** (falla en los dos temas). **[H]** Pasa a
  `blue-700` (6,70:1): el hover oscurece en lugar de aclarar. Es un cambio de aspecto chico y la única
  forma de mantener texto blanco en AA.
- **Estados.** `peligro`/`exito`/`aviso` son texto; `*-fondo` son los fondos tintados compuestos (oscuro:
  `red-900/30`, `green-900/30`, `amber-900/30` sobre `superficie`, calculados; claro: los de la capa manual);
  `*-borde` son decorativos. `peligro-solido` es el botón destructivo (`red-600`, blanco 4,83:1).
- **Marca.** `oro`/`oro-claro`/`oro-oscuro` salen de `tailwind.config.js` (oscuro) y de la capa manual
  (claro). ↑ `oro-claro` en claro sube de `#8a6d2f` (4,45:1 sobre `superficie`) a `#896c2f` (4,51).
  **`oro-oscuro` no entra al test**: se usa sólo en el separador `aria-hidden` de `LogoAxioma.jsx:23`, que es
  decorativo (WCAG 1.4.3 exime la decoración). Si alguien lo usa para texto con contenido, falla el test
  del §6.3 (lista de exentos cerrada).
- **Burbuja del usuario** (chat): hoy `#1e3a5f` en línea (`Message.jsx:49`). ↑ `texto-suave` encima daba
  4,49:1; baja a `#1d385c` (4,62:1). Claro: `blue-100`.
- **Modo comando** (`BottomBar.jsx:356-373`): hoy `orange-500` con texto blanco = **2,80:1** en los dos
  temas. ↑ Pasa a `orange-700` `#c2410c` (5,18:1).
- **Facetas.** **[H]** `FACET_COLORS` (`useJaxStore.js:71-78`) son colores de identidad que hoy se usan
  **como color de texto** (etiqueta del mensaje, `FacetCard`, chips de AdminCosts/AdminFacetBindings). En
  oscuro fallan tres: `jax_local` `#3b82f6` (3,98 sobre `superficie`), `jekyll` `#6366f1` (3,27), `ada` y
  DALL·E `#7c3aed` (2,57). Suben al tono 400 de su misma familia. En claro, cada una va al tono 700 de su
  familia; `jacobs` (blanco) pasa a `texto` en claro, porque blanco sobre blanco no se ve. Se aplica la
  decisión 6 (subir lo justo) a colores que no son grises: lo pongo como pregunta en §13.
- **Ojo HAL** (`getEyeState`, `useJaxStore.js:498-518`): **no lleva tokens propios**, reutiliza:
  KILL SWITCH → `peligro`, DALL·E → `faceta-imagen`, LAS MANOS caído (`#374151`, 1,42:1 sobre `superficie`)
  → `texto-tenue`, GATE → `aviso`, Jacobs corriendo → `faceta-jacobs`, reposo → `faceta-jax-local`.

### 3.3 Pares declarados y contraste medido (88 pares, 0 fallan)

"Mín." es lo que exige AA para ese par. El PR 1 declara **esta** matriz en código (§6); la tabla de acá es
la foto del 2026-09-14 y el test la vuelve a calcular en cada corrida.

| Texto / primer plano | Fondo | Mín. | Oscuro | Claro |
|---|---|---|---|---|
| `texto-fuerte` | `fondo` | 4,5 | 16,30 | 19,28 |
| `texto` | `fondo` | 4,5 | 14,48 | 17,06 |
| `texto-suave` | `fondo` | 4,5 | 6,96 | 7,24 |
| `texto-tenue` | `fondo` | 4,5 | 5,50 | 4,75 |
| `acento-texto` | `fondo` | 4,5 | 6,76 | 6,67 |
| `info` | `fondo` | 4,5 | 7,02 | 6,41 |
| `peligro` | `fondo` | 4,5 | 6,45 | 6,18 |
| `exito` | `fondo` | 4,5 | 10,25 | 4,79 |
| `aviso` | `fondo` | 4,5 | 10,69 | 4,80 |
| `oro` | `fondo` | 4,5 | 7,81 | 5,75 |
| `oro-claro` | `fondo` | 4,5 | 12,31 | 4,72 |
| `faceta-jax-local` | `fondo` | 4,5 | 7,02 | 6,41 |
| `faceta-jekyll` | `fondo` | 4,5 | 5,98 | 7,55 |
| `faceta-hyde` | `fondo` | 4,5 | 6,37 | 4,95 |
| `faceta-hipatia` | `fondo` | 4,5 | 7,04 | 5,24 |
| `faceta-thot` | `fondo` | 4,5 | 8,31 | 4,80 |
| `faceta-kimi` | `fondo` | 4,5 | 7,35 | 5,12 |
| `faceta-ada` | `fondo` | 4,5 | 6,56 | 6,79 |
| `faceta-jacobs` | `fondo` | 4,5 | 17,85 | 17,06 |
| `faceta-imagen` | `fondo` | 4,5 | 6,56 | 6,79 |
| `texto-fuerte` | `superficie` | 4,5 | 13,35 | 18,41 |
| `texto` | `superficie` | 4,5 | 11,87 | 16,30 |
| `texto-suave` | `superficie` | 4,5 | 5,71 | 6,92 |
| `texto-tenue` | `superficie` | 4,5 | 4,51 | 4,54 |
| `acento-texto` | `superficie` | 4,5 | 5,54 | 6,37 |
| `info` | `superficie` | 4,5 | 5,75 | 6,12 |
| `peligro` | `superficie` | 4,5 | 5,29 | 5,91 |
| `exito` | `superficie` | 4,5 | 8,40 | 4,58 |
| `aviso` | `superficie` | 4,5 | 8,76 | 4,58 |
| `oro` | `superficie` | 4,5 | 6,40 | 5,49 |
| `oro-claro` | `superficie` | 4,5 | 10,09 | 4,51 |
| `faceta-jax-local` | `superficie` | 4,5 | 5,75 | 6,12 |
| `faceta-jekyll` | `superficie` | 4,5 | 4,90 | 7,21 |
| `faceta-hyde` | `superficie` | 4,5 | 5,22 | 4,73 |
| `faceta-hipatia` | `superficie` | 4,5 | 5,77 | 5,01 |
| `faceta-thot` | `superficie` | 4,5 | 6,81 | 4,58 |
| `faceta-kimi` | `superficie` | 4,5 | 6,03 | 4,89 |
| `faceta-ada` | `superficie` | 4,5 | 5,38 | 6,49 |
| `faceta-jacobs` | `superficie` | 4,5 | 14,63 | 16,30 |
| `faceta-imagen` | `superficie` | 4,5 | 5,38 | 6,49 |
| `texto-fuerte` | `hundido` | 4,5 | 16,30 | 20,17 |
| `texto` | `hundido` | 4,5 | 14,48 | 17,85 |
| `texto-suave` | `hundido` | 4,5 | 6,96 | 7,58 |
| `texto-tenue` | `hundido` | 4,5 | 5,50 | 4,97 |
| `acento-texto` | `hundido` | 4,5 | 6,76 | 6,98 |
| `info` | `hundido` | 4,5 | 7,02 | 6,70 |
| `peligro` | `hundido` | 4,5 | 6,45 | 6,47 |
| `exito` | `hundido` | 4,5 | 10,25 | 5,02 |
| `aviso` | `hundido` | 4,5 | 10,69 | 5,02 |
| `oro` | `hundido` | 4,5 | 7,81 | 6,02 |
| `oro-claro` | `hundido` | 4,5 | 12,31 | 4,94 |
| `faceta-jax-local` | `hundido` | 4,5 | 7,02 | 6,70 |
| `faceta-jekyll` | `hundido` | 4,5 | 5,98 | 7,90 |
| `faceta-hyde` | `hundido` | 4,5 | 6,37 | 5,18 |
| `faceta-hipatia` | `hundido` | 4,5 | 7,04 | 5,48 |
| `faceta-thot` | `hundido` | 4,5 | 8,31 | 5,02 |
| `faceta-kimi` | `hundido` | 4,5 | 7,35 | 5,36 |
| `faceta-ada` | `hundido` | 4,5 | 6,56 | 7,10 |
| `faceta-jacobs` | `hundido` | 4,5 | 17,85 | 17,85 |
| `faceta-imagen` | `hundido` | 4,5 | 6,56 | 7,10 |
| `texto-fuerte` | `superficie-2` | 4,5 | 9,45 | 16,36 |
| `texto` | `superficie-2` | 4,5 | 8,40 | 14,48 |
| `acento-texto` | `acento-fondo` | 4,5 | 5,13 | 6,51 |
| `texto` | `acento-fondo` | 4,5 | 11,00 | 16,64 |
| `info` | `info-fondo` | 4,5 | 5,26 | 6,16 |
| `texto` | `info-fondo` | 4,5 | 10,84 | 16,40 |
| `peligro` | `peligro-fondo` | 4,5 | 5,07 | 5,91 |
| `texto` | `peligro-fondo` | 4,5 | 11,38 | 16,32 |
| `exito` | `exito-fondo` | 4,5 | 7,40 | 4,79 |
| `texto` | `exito-fondo` | 4,5 | 10,45 | 17,05 |
| `aviso` | `aviso-fondo` | 4,5 | 7,92 | 4,84 |
| `texto` | `aviso-fondo` | 4,5 | 10,72 | 17,22 |
| `sobre-color` | `acento` | 4,5 | 5,38 | 5,38 |
| `sobre-color` | `acento-hover` | 4,5 | 6,98 | 6,98 |
| `sobre-color` | `accion` | 4,5 | 5,17 | 5,17 |
| `sobre-color` | `accion-hover` | 4,5 | 6,70 | 6,70 |
| `sobre-color` | `peligro-solido` | 4,5 | 4,83 | 4,83 |
| `sobre-color` | `peligro-solido-hover` | 4,5 | 6,47 | 6,47 |
| `sobre-color` | `modo-comando` | 4,5 | 5,18 | 5,18 |
| `texto` | `burbuja-usuario` | 4,5 | 9,61 | 14,63 |
| `texto-suave` | `burbuja-usuario` | 4,5 | 4,62 | 6,21 |
| `fondo` | `texto-fuerte` | 4,5 | 16,30 | 19,28 |
| `borde-control` | `fondo` | 3,0 | 3,70 | 3,15 |
| `foco` | `fondo` | 3,0 | 4,85 | 3,52 |
| `borde-control` | `superficie` | 3,0 | 3,03 | 3,01 |
| `foco` | `superficie` | 3,0 | 3,98 | 3,36 |
| `borde-control` | `hundido` | 3,0 | 3,70 | 3,29 |
| `foco` | `hundido` | 3,0 | 4,85 | 3,68 |

Qué **no** está en la matriz, y por eso queda prohibido (lo impide la regla de §7 y lo vigila la revisión):
`texto-suave`, `texto-tenue` y los colores de estado **sobre `superficie-2`** (en oscuro `texto-suave` da
4,04 y `peligro` 3,74). Un elemento cuyo hover es `bg-superficie-2` pone `text-texto` en ese hover.

---

## 4. Mapeo en Tailwind

`tailwind.config.js`, dentro de `theme.extend.colors`, un color por token con opacidad:

```js
// Generado a partir de la lista de tokens: una entrada por token.
const token = (nombre) => `rgb(var(--${nombre}) / <alpha-value>)`
colors: {
  fondo: token('fondo'),
  superficie: { DEFAULT: token('superficie'), 2: token('superficie-2') },
  texto: { DEFAULT: token('texto'), fuerte: token('texto-fuerte'), suave: token('texto-suave'), tenue: token('texto-tenue') },
  // ... el resto de la lista de §3.2 con la misma forma
}
```

- **La lista de nombres sale de un módulo** (`frontend/src/tema/tokens.js`, exporta los nombres y la
  matriz de pares) que importan `tailwind.config.js`, el test y quien necesite un token en JS. Los
  **valores** están sólo en `tokens.css`; `tokens.js` tiene nombres y pares, no colores. Así no hay dos
  fuentes de verdad de un valor, y un token nuevo que no esté en `tokens.css` lo detecta el test.
- `oro` y `hal` conviven en el `extend` hasta que se migran sus usos: `oro` pasa a apuntar a los tokens en el
  PR 1 (sus tres usos están en `LogoAxioma`, que es de la barra de usuario); `hal` se borra en el PR 4.
- Se agregan `fill-*` y `stroke-*` gratis (Tailwind los genera de `colors`): son la vía de los SVG.
- No se activa `darkMode` de Tailwind: el tema lo resuelven las variables, no el prefijo `dark:`. Un
  `dark:` en el código después del PR 1 es un error (lo revisa el test de §6.4).

---

## 5. Flujo de `theme_default` sin parpadeo

### 5.1 Semántica

- **Elección del usuario** = `localStorage['jax_theme']` (`'dark'`|`'light'`), que hoy sólo se escribe al
  tocar el interruptor. Se conserva la clave y sus valores: quien ya eligió, sigue igual al desplegar.
- **Predeterminado del sistema** = `axioma_config.theme_default`.
- Regla: si hay elección, gana la elección; si no, el predeterminado. El interruptor escribe una
  elección. **[H]** No hay opción "volver al predeterminado" en la UI en esta ronda (§11).

### 5.2 Cómo llega el predeterminado antes de pintar

El frontend es estático (build de Vite servido por nginx en la VM atemai); **no hay HTML renderizado por
el servidor** donde inyectar el valor, y la config del admin (`GET /api/admin/config`) exige superadmin,
así que no sirve para Login ni para un operador.

**Diseño [H]:**

1. **Endpoint público nuevo** `GET /api/apariencia` (sin autenticación) en un router nuevo
   `backend/api/apariencia.py`. Devuelve **sólo** `{"theme_default": "dark"|"light"}`. Lee
   `axioma_config` por PRIMARY KEY (`WHERE config_key = 'theme_default'`, tabla chica; EXPLAIN en el test
   como pide LAS CUATRO). Valida el valor contra la lista cerrada `{'dark','light'}` y, si la fila falta o
   trae otra cosa, responde `'dark'` (el `DEFAULT_CONFIG`) — el valor viaja a un atributo del DOM, así que
   nada fuera de la lista llega al cliente.
2. **Script en línea en `index.html`**, en el `<head>` antes de cualquier CSS: lee la elección; si no hay,
   lee `localStorage['jax_theme_default']` (el último predeterminado conocido); si tampoco, `'dark'`. Pone
   `data-tema` (y la clase `light-mode` mientras conviva la capa vieja) **antes del primer pintado**. Hoy
   esto lo hace `main.jsx:8-11`, que es un módulo diferido y corre *después* de que el HTML se parsea: puede
   parpadear. El script en línea es síncrono y no cuesta una petición. Se borra el bloque de `main.jsx`.
3. **Después de montar**, `useTema` pide `/api/apariencia` una vez, guarda el valor en
   `jax_theme_default` y, **si el usuario no eligió** y el valor cambió, lo aplica. Sólo puede haber un
   cambio visible en dos casos: la primera visita de un navegador cuando el predeterminado es claro, o
   después de que el admin cambió el predeterminado. Es el único parpadeo posible y está acotado.
4. Al guardar la config, `AdminSettings` actualiza `jax_theme_default` en ese navegador para que el admin
   vea el efecto sin recargar.

**Por qué no las alternativas:**
- *Meter el valor en `/api/auth/me`*: llega después del login; en Login y Reset no hay sesión.
- *Generar un `.js` estático en cada guardado de config*: acopla el backend al directorio que sirve nginx en
  otra máquina.
- *Templatizar `index.html` en nginx (SSI/sub_filter)*: agrega configuración de servidor fuera del repo por
  un valor de dos letras.

**Exposición sin autenticación:** el endpoint revela sólo la preferencia visual por defecto de la
instancia, que cualquiera ve igual abriendo el Login. No toca otras claves de `axioma_config` (una lista
blanca de **una** clave, no un prefijo excluido — el `smtp.*` vive en la misma tabla y el endpoint no puede
devolverlo aunque se agreguen claves). Es de sólo lectura, sin parámetros. Riesgo de carga: una consulta
por PK por carga de página; se mide en la prueba de carga (§9). **Sin caché en el backend [H]**: la lectura
por PK de una tabla chica no justifica un caché con invalidación entre procesos (LAS CUATRO: "sin medición
previa, no hay caché nuevo"); si la prueba de carga dice otra cosa, se agrega con su invalidación en el
mismo commit. Respuesta con `Cache-Control: no-cache` para que un cambio del admin se vea en la siguiente
carga.

**CSP:** un script en línea choca con una `Content-Security-Policy` que no permita `'unsafe-inline'`. En el
repo no hay CSP declarada (grep de `Content-Security-Policy`/`add_header`: 0), pero nginx vive en atemai y
no lo verifiqué. Paso obligatorio del PR 1: leer los encabezados que sirve `axioma-ia.io`; si hay CSP, se
agrega el hash `sha256-` del script, no `'unsafe-inline'`.

### 5.3 El hook

`store/useTheme.js` pasa a `store/useTema.js` con la misma interfaz (`{ theme, toggleTheme }`) más
`predeterminado`. El nombre de la clave de `localStorage` y la aplicación al DOM salen de **una** función
compartida con el script de `index.html` (`tema/aplicarTema.js`), para que las dos rutas no se desalineen;
el script en línea es la copia mínima y un test compara que ambos aplican lo mismo para las cuatro
combinaciones (elección sí/no × predeterminado claro/oscuro).

---

## 6. El test de contraste

Archivo `frontend/src/tema/contraste.test.js` (entorno `node`, como el test actual, porque lee CSS por `fs`:
vitest excluye el CSS del pipeline y un `?raw` llega vacío — medido en `lightModeOverrides.test.js:5-6`).

1. **Parsea `tokens.css`**: extrae cada `--nombre: R G B;` de `:root` (oscuro) y de
   `:root[data-tema="claro"]` (claro).
2. **Completitud**: todo nombre de `tokens.js` tiene valor en los dos temas, y todo valor de `tokens.css`
   tiene nombre en `tokens.js`. Un token a medias falla.
3. **Contraste**: para cada par de la matriz (`tokens.js`), calcula la razón WCAG (luminancia relativa
   sRGB, `(L1+0.05)/(L2+0.05)`) en los dos temas y exige el mínimo del par (4,5 texto, 3 componente). El
   mensaje de falla nombra token, fondo, tema, razón y mínimo. La lista de **exentos** (hoy sólo
   `oro-oscuro`, decorativo) es cerrada y cada entrada lleva su motivo.
4. **Uso**: escanea los fuentes (`import.meta.glob`, igual que el test actual) y falla si un archivo **ya
   migrado** usa una clase de color vieja (`slate-*`, `red-*`, …), un hex o un `dark:`. "Ya migrado" es
   una lista en `tokens.js` que cada PR amplía; el PR 4 la reemplaza por "todos". Esto es lo que impide
   que un grupo migrado se "desmigre" y lo que garantiza que el PR 4 terminó.
5. **Control negativo**: un caso con un par inventado que falla (p. ej. `texto-tenue` viejo `#64748b` sobre
   `#0f172a` = 3,75) debe dar rojo; si la función de contraste no falla con él, no valida nada. Se ve en
   rojo una vez antes de verde, como el canario de CI.

`lightModeOverrides.test.js` se borra en el PR que borra los overrides de rojos y verdes (PR 1 migra
`AlertaError`, pero los rojos de admin viven hasta el PR 2: el test viejo **se queda hasta el PR 2** y se
achica en cada PR). **[H]** Borrarlo en el PR 1 dejaría sin guardia a las pantallas de admin que todavía
dependen de esos overrides.

El piso de vitest de `.github/workflows/policy.yml` (hoy `numPassedTests !== 125` en `master`; la etapa 2
de usuarios lo sube) se pone en cada PR al número **medido**, leyendo el valor vigente del archivo al
rebasar, con comentario.

---

## 7. Reglas de migración

### 7.1 Clases

| Hoy | Token |
|---|---|
| `bg-hal-bg`, `bg-slate-900` (página) | `bg-fondo` |
| `bg-slate-900` dentro de tarjeta (campo) | `bg-hundido` |
| `bg-slate-800` | `bg-superficie` |
| `bg-slate-700`, `bg-slate-600`, `hover:bg-slate-700/600/750` | `bg-superficie-2` (+ `text-texto` en ese estado) |
| `bg-slate-800/30`, `/50`, `bg-slate-900/50…80` | `bg-superficie/NN` o `bg-fondo/NN` (sin texto de estado encima) |
| `border-slate-800`, `border-slate-700`, `divide-slate-800/50` | `border-borde`, `divide-borde/50` |
| `border-slate-600` en campo o control | `border-borde-control` |
| `text-slate-100` | `text-texto-fuerte` |
| `text-slate-200`, `text-slate-300`, `text-hal-text` | `text-texto` |
| `text-slate-400` | `text-texto-suave` |
| `text-slate-500`, `-600`, `-700`, `placeholder-slate-600` | `text-texto-tenue`, `placeholder-texto-tenue` |
| `text-white` sobre color sólido | `text-sobre-color` |
| `bg-purple-600` / `hover:bg-purple-700` / `border-purple-500` | `bg-acento` / `hover:bg-acento-hover` / `border-acento` |
| `text-purple-300/400` | `text-acento-texto` |
| `bg-purple-900/40…` | `bg-acento-fondo` |
| `bg-blue-600` / `hover:bg-blue-500` | `bg-accion` / `hover:bg-accion-hover` |
| `text-blue-300/400` | `text-info` |
| `focus:border-blue-500`, `ring-blue-500`, `focus:border-purple-500` | `focus:border-foco`, `ring-foco` |
| `text-red-200…500` / `bg-red-900/30…950` / `border-red-700/800` | `text-peligro` / `bg-peligro-fondo` / `border-peligro-borde` |
| `bg-red-500/600` + blanco / `hover:bg-red-700` | `bg-peligro-solido` / `hover:bg-peligro-solido-hover` |
| verdes (`green-*`, `emerald-400`) | `exito`, `exito-fondo`, `exito-borde` |
| `amber-*`, `yellow-*`, `orange-300/400` como aviso | `aviso`, `aviso-fondo`, `aviso-borde` |
| `bg-black/60` (velo de modal) | `bg-fondo/70` **[H]** (un velo negro sobre claro oscurece todo; el velo toma el color del fondo del tema) |
| `text-oro*` | `text-oro*` (ahora tokens; mismo nombre) |

Casos que la tabla no cubre (p. ej. `bg-gray-500`, `bg-yellow-500` como punto de estado) se resuelven en el
PR del grupo con el token de estado más cercano, y si ninguno sirve se agrega un token **con su par en la
matriz**, nunca un color suelto.

### 7.2 Constantes compartidas

Se migran **primero** dentro de su PR, porque arrastran muchos usos: `INPUT`/`LABEL`/`BOTON`
(`AdminSmtp.jsx:20-22`, PR 2), `BOTON`/`BOTON_NEUTRO` (`BarraUsuario.jsx:67-68`, PR 1), `AlertaError`
(PR 1: `text-red-400` → `text-peligro`). **[H]** No se crea una librería de componentes nueva en esta
ronda: las constantes se quedan donde están, sólo cambian sus clases (§11).

### 7.3 Hex en JS y estilos en línea

- **SVG** (`Login.jsx:149-153`, `HalEye.jsx:32,63,70`): `fill="#0f172a"` → `className="fill-fondo"`,
  `stroke="#1e293b"` → `stroke-superficie`. Los atributos de presentación SVG no aceptan `var()` de forma
  fiable; la clase sí.
- **Colores que vienen de datos** (`FACET_COLORS`, `getEyeState`): el store deja de guardar hex y guarda
  **el nombre del token** (`'faceta-hyde'`). Un helper `colorToken(nombre, alfa = 1)` en `tema/tokens.js`
  devuelve `` `rgb(var(--${nombre}) / ${alfa})` `` y valida el nombre contra la lista (nombre desconocido →
  `texto-suave`, que es el respaldo actual `#94a3b8`). Con eso:
  - `style={{ color }}` → `style={{ color: colorToken(t) }}`;
  - el patrón de concatenar alfa al hex (`color + '20'`, `color + '25'`, `` `${color}60` ``, en Message,
    AdminCosts, AdminFacetBindings, BottomBar) → `colorToken(t, 0.12)` etc. Deja de depender de que el
    color sea hex de 6 dígitos;
  - HalEye ya usa una variable CSS (`--eye-color`); recibe `colorToken(...)` y no cambia su CSS.
- **Hex sueltos en línea** (`'#3b82f6'` en RightPanel y PipelineModal, `'#7c3aed'` en BottomBar,
  `rgba(0,0,0,0.7)` en PipelineModal) → la clase del token (`bg-accion`, `bg-acento`, `bg-fondo/70`), no un
  `style`.
- **Leer un token en JS con `getComputedStyle`** sólo si una API exige un color resuelto (canvas, una
  librería). Hoy no hay ningún caso; si aparece, va en el mismo helper.

---

## 8. Rollout

Cada PR: rama propia desde `master` actualizado, CI verde por `headSha`, revisión, despliegue del frontend
(y del backend en el PR 1), verificación en vivo en los **dos** temas, número de rendimiento en la
Biblioteca. Los tokens conviven con las clases viejas, así que después de cada PR la app entera funciona:
las pantallas migradas por tokens, las no migradas por la capa vieja.

| PR | Contenido |
|---|---|
| **1 · Base** | `tema/tokens.css` + `tema/tokens.js` (47 tokens, 88 pares); `tailwind.config.js`; `index.css` importa los tokens, `body` y barras de desplazamiento pasan a tokens, y se borran los overrides **de lo que este PR migra** (`bg-hal-bg`/`text-hal-text` no, siguen en Dashboard; sí `text-oro*`); `index.html` con el script de tema; `useTema` + `aplicarTema`; endpoint `GET /api/apariencia` con tests (valor válido, fila ausente, valor fuera de lista, no filtra otras claves, sin token) y EXPLAIN; `AdminSettings` refresca el predeterminado local; `@fontsource/inter`; test de contraste (§6); **consumidores**: `Login.jsx`, `ResetPassword.jsx` (misma familia visual y comparte clases con Login **[H]**), `BarraUsuario.jsx`, `LogoAxioma.jsx`, `AlertaError.jsx`, `PasswordInput.jsx` (lo usan Login y Reset; su test de `toHaveClass` se actualiza). i18n: ninguna cadena visible nueva salvo que la revisión pida una. DEUDA §1.3 corregida. |
| **2 · Admin** | `pages/admin/*` y `components/admin/*` (AdminMotors, AdminFacetsModels, AdminUsers, AdminModelCatalog, AdminSmtp, AdminSettings, AdminRepository, AdminCosts, AdminFacetBindings…), `pages/Admin.jsx`. Constantes `INPUT/LABEL/BOTON`. Borra los overrides de rojos/verdes y `lightModeOverrides.test.js` **si** ya no queda uso fuera de admin; si queda (chat), el test se achica a esos archivos y se borra en el PR 3. |
| **3 · Chat y pipelines** | `components/CenterPanel/*`, `components/chat/*`, `components/BottomBar/*` (incluye PipelineModal), `components/RightPanel/*`, `components/LeftPanel/*`, `components/HalEye/*`, `store/useJaxStore.js` (`FACET_COLORS` y `getEyeState` a nombres de token), `components/Notifications/*`. |
| **4 · El resto y cierre** | Lo que quede (Dashboard, `hal-*`), la lista "ya migrado" pasa a "todos", se borra **el resto** de la capa `html.light-mode`, la clase `light-mode` del script y del hook, y `hal` de `tailwind.config.js`. DEUDA: el ítem de tema se cierra. |

**Orden con la rama concurrente (etapa 2 de usuarios, `/home/fruiz/worktrees/jax-platform-etapa2`).** Su
Task 4b toca `Login.jsx` (muestra `avisoSesion` con `AlertaError`), `api/client.js`, `useJaxStore.js` e
i18n, y sube el piso de vitest. **Se mergea primero la etapa 2** (está en ejecución y es de seguridad);
el PR 1 de tema se rebasa sobre ella antes de abrirse, migra el `AlertaError` que la 4b ya usa (el aviso
queda con tokens sin tocar la 4b) y fija el piso de vitest leyendo el valor que dejó la etapa 2. Si el
dueño prefiere el orden inverso, la 4b es la que rebasa y el conflicto se reduce a clases en `Login.jsx`.
El PR 3 toca `useJaxStore.js` en zonas distintas a la 4b (colores vs. `avisoSesion`).

---

## 9. Rendimiento (LAS CUATRO)

- **Indexing.** La única consulta nueva es `GET /api/apariencia`: una fila por PRIMARY KEY de
  `axioma_config`. El test ejecuta `EXPLAIN` sobre la consulta real y exige `key = PRIMARY` y ni
  `Using filesort` ni `Using temporary`.
- **Cache.** Sin caché nuevo en el backend (§5.2, con su porqué). En el cliente, `jax_theme_default` en
  `localStorage` **es** un caché: se invalida en cada carga (se refresca con la respuesta del endpoint) y al
  guardar la config en ese navegador. El valor viejo, en el peor caso, dura una carga.
- **Async.** El endpoint usa el pool `aiomysql` existente. La fuente no bloquea el render:
  `@fontsource` declara `font-display: swap` (se verifica en el CSS generado). Sólo se importan los
  subconjuntos `latin` de los pesos **usados**: 400, 500, 600 y 700 (ver §1.2; `font-light` es de Plex
  Serif). El script en línea es síncrono a propósito, pero es de unas líneas y no hace red.
- **Carga y medidas.** Antes y después de cada PR, sobre el build de producción: tamaño del JS y del CSS
  (crudo y gzip, línea de base en §1.2) y peso de las fuentes woff2 agregadas; *First Contentful Paint* y
  *Cumulative Layout Shift* del Login con Lighthouse o el panel de rendimiento, en frío, en los dos temas.
  Criterio: el CSS puede crecer por los tokens pero **no** el JS más de lo que agregan `tokens.js` y el hook;
  el CLS por el cambio de fuente se mide y, si pasa de 0,1, se ajusta la fuente de respaldo
  (`size-adjust`). `GET /api/apariencia` pasa por prueba de carga con concurrencia real (p95 y peticiones
  por segundo, con el número escrito en la Biblioteca) antes del GO del PR 1: se pide en cada carga de
  página, incluido el Login público.

---

## 10. Pruebas

- **Contraste y completitud** (§6), con control negativo visto en rojo.
- **Tema**: `aplicarTema` para las 4 combinaciones (elección × predeterminado); el interruptor escribe la
  elección y pone `data-tema`; con elección guardada, un predeterminado distinto **no** cambia el tema; sin
  elección, sí. El script en línea y `aplicarTema` coinciden (el test extrae el script de `index.html`).
- **Backend** (`pytest`, con DB): los cinco casos del endpoint en §8 y el EXPLAIN.
- **Pantallas migradas**: los tests existentes (Login, Reset, BarraUsuario, PasswordInput) siguen verdes;
  el de `toHaveClass` se actualiza al token.
- **i18n**: si una etiqueta nueva aparece (p. ej. el `aria-label` del interruptor ya existe como
  `etiquetaTema`), va en `es.js` y `en.js`; el test de claves existente la cubre.
- **Manual en vivo, por PR**: cada pantalla del grupo en claro y en oscuro, con el predeterminado en
  claro y en oscuro y un navegador limpio (sin `localStorage`), para ver que no hay parpadeo.

---

## 11. Fuera de alcance

- Seguir el tema del sistema operativo (`prefers-color-scheme`) o una opción "Sistema" en el interruptor.
- Un botón "volver al predeterminado" (el usuario que eligió puede volver a elegir).
- Guardar la elección del usuario en la base (hoy es por navegador y sigue así).
- Rediseño visual, librería de componentes o cambios de espaciado/tipografía más allá de cargar Inter.
- Contraste de estados deshabilitados (WCAG los exime) y de imágenes generadas.
- Cambiar los colores de identidad de las facetas en la base o en `jax` (sólo cambia cómo los pinta el
  frontend).

---

## 12. Decisiones tomadas por mí dentro del marco ([H])

1. `data-tema` en lugar de reutilizar la clase `light-mode` — convivencia sin choque con la capa vieja.
2. Endpoint público de una clave + script en línea + último valor en `localStorage` — única vía sin
   sesión y sin tocar nginx; parpadeo acotado a primera visita o cambio del admin.
3. Sin caché backend para `/api/apariencia` — no hay medición que lo pida.
4. `accion-hover` a `blue-700` — `blue-500` con blanco da 3,68:1.
5. Facetas suben al tono 400 (oscuro) / 700 (claro); `jacobs` a `texto` en claro.
6. Velo de modal con el color del fondo del tema, no negro.
7. `lightModeOverrides.test.js` vive hasta el PR 2/3, no se borra en el PR 1.
8. `ResetPassword` y `PasswordInput` entran en el PR 1 junto a Login.
9. Burbuja del usuario de `#1e3a5f` a `#1d385c` para que `texto-suave` encima pase AA.
10. Etapa 2 de usuarios se mergea antes que el PR 1.

## 13. Preguntas abiertas para el dueño

1. **Facetas**: ¿aceptás que los colores de identidad que fallan AA en oscuro (`jax_local`, `jekyll`, `ada`,
   DALL·E) suban al tono 400? La otra vía es dos tokens por faceta (marca para puntos y bordes, texto para
   etiquetas), que conserva el tono exacto al costo de 9 tokens más.
2. **Orden** con la etapa 2: ¿etapa 2 primero (propuesto) o tema primero?
3. **CSP en atemai**: ¿hay alguna política de encabezados en el nginx de `axioma-ia.io` que yo no vea
   desde el repo? Se verifica igual en el PR 1.

## 14. Riesgos

| Riesgo | Mitigación |
|---|---|
| Una pantalla queda a medio migrar y se ve mal en un tema | La capa vieja no se borra hasta que su grupo migra; el test de §6.4 impide volver atrás en lo migrado. |
| El script en línea lo bloquea una CSP | Verificación de encabezados en el PR 1; hash `sha256-`, nunca `'unsafe-inline'`. |
| Conflictos con la etapa 2 en `Login.jsx`/`useJaxStore.js` | Orden de merge de §8; rebase antes de abrir. |
| Parpadeo en la primera visita con predeterminado claro | Acotado y medido; se registra el caso. |
| Cambio de fuente mueve el layout (CLS) | Medición de §9 y `size-adjust` si hace falta. |
| El endpoint público se usa para sondear la config | Lista blanca de una clave, valor validado contra una lista cerrada, sin parámetros. |
| Un token nuevo sin par en la matriz se usa como texto | Completitud del §6.2 + regla de §7.1 ("nunca un color suelto"); la revisión lo mira. |
| El `aviso` de claro (`#b45309`) queda justo (4,58:1 sobre `superficie`) | El test lo vigila; cualquier cambio de superficie lo rompe en rojo, no en silencio. |

## 15. Autorrevisión

- **Marcadores vacíos**: ninguno; cada valor de §3 lo generó el script y la matriz da 0 fallas.
- **Contradicciones revisadas**: "borrar la capa vieja" (decisión 1) vs. convivencia (decisión 5) se
  resuelven borrando por grupo y del todo en el PR 4. "Reemplazar `lightModeOverrides.test.js`" (decisión 4)
  se cumple en el PR 2/3, no en el PR 1, por la razón de §6 — lo marco como [H] porque ajusta el momento, no
  el qué. "Conservar el look" vs. AA: los cambios de aspecto están listados (↑ en §3.2) y son los mínimos.
- **Alcance**: sólo frontend y un endpoint público de lectura; nada en `jax`, nada en la tabla de config.
